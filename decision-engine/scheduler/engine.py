"""
Scheduler Engine - Core Scheduling Orchestration

The SchedulerEngine is the main orchestrator that:
1. Manages precision flavours (low/medium/high power variants)
2. Maintains quality credit ledger
3. Fetches carbon intensity forecasts
4. Evaluates scheduling policies/strategies to compute traffic distributions
5. Exports Prometheus metrics for monitoring

Each TrafficSchedule gets its own SchedulerEngine instance with
independent configuration and state.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional

from prometheus_client import Counter, Gauge
from prometheus_client.core import GaugeMetricFamily, REGISTRY
from prometheus_client.registry import Collector

from .ledger import CreditLedger
from .models import (
    FlavourProfile,
    ForecastSnapshot,
    PolicyResult,
    ScheduleDecision,
    SchedulerConfig,
    ScalingDirective,
    precision_key,
)
from .strategies import CreditGreedyPolicy, ForecastAwarePolicy, PrecisionTierPolicy, SchedulerPolicy
from .providers import CarbonForecastProvider, DemandEstimator, ForecastManager

_LOGGER = logging.getLogger("scheduler")


class FlavourRegistry:
    """
    Thread-safe in-memory registry of precision flavours.
    
    Stores available FlavourProfile instances (e.g., precision-30, precision-50,
    precision-100) and provides synchronized access for policy evaluation.
    """

    def __init__(self, flavours: Optional[Iterable[FlavourProfile]] = None) -> None:
        """Initialize registry with optional initial flavours."""
        self._lock = threading.Lock()
        self._flavours: Dict[str, FlavourProfile] = {}
        if flavours:
            for flavour in flavours:
                self._flavours[flavour.name] = flavour

    def list(self) -> List[FlavourProfile]:
        """Get list of all registered flavours."""
        with self._lock:
            return list(self._flavours.values())

    def replace(self, flavours: Iterable[FlavourProfile]) -> None:
        """Replace all flavours with new set."""
        with self._lock:
            self._flavours = {f.name: f for f in flavours}

    def upsert(self, flavour: FlavourProfile) -> None:
        """Add or update a single flavour."""
        with self._lock:
            self._flavours[flavour.name] = flavour


# Mapping of policy names to implementation classes
_POLICY_BUILDERS: Dict[str, type[SchedulerPolicy]] = {
    "credit-greedy": CreditGreedyPolicy,
    "forecast-aware": ForecastAwarePolicy,
    "precision-tier": PrecisionTierPolicy,
}

# ============================================================================
# Prometheus Metrics
# Global metrics shared across all scheduler sessions to avoid duplicate
# registrations. Labeled by namespace/schedule to distinguish instances.
# ============================================================================
_METRIC_FLAVOUR = Gauge(
    "schedule_flavour_weight",
    "Weight per flavour",
    ["namespace", "schedule", "flavour"],
)
_METRIC_VALID_UNTIL = Gauge(
    "schedule_valid_until",
    "UNIX epoch of validUntil",
    ["namespace", "schedule"],
)
_METRIC_CREDIT_BALANCE = Gauge(
    "scheduler_credit_balance",
    "Current credit balance",
    ["namespace", "schedule", "policy"],
)
_METRIC_CREDIT_VELOCITY = Gauge(
    "scheduler_credit_velocity",
    "Average credit delta",
    ["namespace", "schedule", "policy"],
)
_METRIC_PRECISION = Gauge(
    "scheduler_avg_precision",
    "Average precision seen",
    ["namespace", "schedule", "policy"],
)
_METRIC_PROCESSING_THROTTLE = Gauge(
    "scheduler_processing_throttle",
    "Throttle factor applied to downstream processing",
    ["namespace", "schedule", "policy"],
)
_METRIC_CEILING = Gauge(
    "scheduler_effective_replica_ceiling",
    "Effective replica ceiling per component",
    ["namespace", "schedule", "component"],
)
_METRIC_POLICY_CHOICE = Counter(
    "scheduler_policy_choice_total",
    "Policy selections per strategy",
    ["namespace", "schedule", "policy", "strategy"],
)
_METRIC_FORECAST = Gauge(
    "scheduler_forecast_intensity",
    "Carbon intensity forecast",
    ["namespace", "schedule", "policy", "horizon"],
)


class ForecastCollector(Collector):
    """
    Custom Prometheus collector that exports forecast metrics with explicit timestamps.
    
    This allows forecast data to be plotted at their target time in the future
    rather than at the current scrape time.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._forecasts: Dict[tuple, tuple[float, float]] = {}  # (labels) -> (value, timestamp)
    
    def set_forecast(self, namespace: str, schedule: str, policy: str, horizon: str, 
                     value: float, timestamp: float) -> None:
        """Set a forecast value with an explicit timestamp."""
        with self._lock:
            key = (namespace, schedule, policy, horizon)
            self._forecasts[key] = (value, timestamp)
    
    def clear_old_forecasts(self, cutoff_time: float) -> None:
        """Remove forecasts older than the cutoff time."""
        with self._lock:
            self._forecasts = {
                key: (val, ts) 
                for key, (val, ts) in self._forecasts.items() 
                if ts > cutoff_time
            }
    
    def collect(self):
        """Generate metrics for Prometheus scraping."""
        with self._lock:
            if not self._forecasts:
                return
            
            # Create a GaugeMetricFamily for timestamped forecasts
            family = GaugeMetricFamily(
                'scheduler_forecast_intensity_timestamped',
                'Carbon intensity forecast with target timestamp',
                labels=['namespace', 'schedule', 'policy', 'horizon']
            )
            
            for (namespace, schedule, policy, horizon), (value, timestamp) in self._forecasts.items():
                family.add_metric(
                    [namespace, schedule, policy, horizon],
                    value,
                    timestamp=timestamp * 1000  # Prometheus expects milliseconds
                )
            
            yield family


# Global forecast collector instance
_FORECAST_COLLECTOR = ForecastCollector()

try:
    REGISTRY.register(_FORECAST_COLLECTOR)
except Exception:
    # Already registered, skip
    pass


def _merge_with_fallback(
    primary: Iterable[FlavourProfile],
    fallback: Iterable[FlavourProfile],
) -> List[FlavourProfile]:
    """
    Merge two strategy lists, with primary overriding fallback.
    
    Args:
        primary: Preferred strategies (from operator)
        fallback: Default strategies (hardcoded)
        
    Returns:
        Merged list sorted by precision (descending)
    """
    merged: Dict[str, FlavourProfile] = {strategy.name: strategy for strategy in fallback}
    for strategy in primary:
        merged[strategy.name] = strategy
    return sorted(merged.values(), key=lambda item: item.precision, reverse=True)


class SchedulerEngine:
    """
    Main scheduler engine coordinating carbon-aware traffic scheduling.
    
    Responsibilities:
    - Manage precision strategy registry
    - Track quality credits via ledger
    - Fetch carbon intensity forecasts
    - Evaluate scheduling policy to compute traffic distribution
    - Export Prometheus metrics
    
    Each TrafficSchedule resource gets its own SchedulerEngine instance.
    """

    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
        namespace: str = "default",
        name: str = "default",
        component_bounds: Optional[Mapping[str, Mapping[str, int]]] = None,
        flavours: Optional[Iterable[FlavourProfile]] = None,
    ) -> None:
        """
        Initialize scheduler engine.
        
        Args:
            config: Scheduler configuration (defaults from environment if None)
            namespace: Kubernetes namespace of TrafficSchedule
            name: Name of TrafficSchedule resource
            component_bounds: Min/max replica constraints per component
            flavours: Precision flavours to use (defaults if None)
        """
        self.namespace = namespace
        self.name = name
        self.component_bounds: Dict[str, Dict[str, int]] = {}
        if component_bounds:
            for comp, bounds in component_bounds.items():
                if not isinstance(bounds, Mapping):
                    continue
                entries: Dict[str, int] = {}
                for key in ("min", "max"):
                    if key not in bounds or bounds[key] is None:
                        continue
                    try:
                        entries[key] = int(bounds[key])
                    except (TypeError, ValueError):
                        continue
                if entries:
                    self.component_bounds[comp] = entries
        self.config = config or self._load_config()
        self.ledger = CreditLedger(
            target_error=self.config.target_error,
            credit_min=self.config.credit_min,
            credit_max=self.config.credit_max,
            window_size=self.config.smoothing_window,
        )
        default_flavours = self._load_default_flavours()
        provided_flavours = list(flavours) if flavours else []
        # Use only provided flavours if available, otherwise use defaults
        initial_flavours = provided_flavours if provided_flavours else default_flavours
        self._fallback_flavours = list(default_flavours)
        self.registry = FlavourRegistry(initial_flavours)
        self.forecast_manager = ForecastManager(CarbonForecastProvider(), DemandEstimator())
        self.policy = self._build_policy(self.config.policy_name)
        self._lock = threading.Lock()

        self._metric_flavour = _METRIC_FLAVOUR
        self._metric_valid_until = _METRIC_VALID_UNTIL
        self._metric_credit_balance = _METRIC_CREDIT_BALANCE
        self._metric_credit_velocity = _METRIC_CREDIT_VELOCITY
        self._metric_precision = _METRIC_PRECISION
        self._metric_processing_throttle = _METRIC_PROCESSING_THROTTLE
        self._metric_ceiling = _METRIC_CEILING
        self._metric_policy_choice = _METRIC_POLICY_CHOICE
        self._metric_forecast = _METRIC_FORECAST

    def _load_config(self) -> SchedulerConfig:
        return SchedulerConfig(
            target_error=float(os.getenv("TARGET_ERROR", "0.1")),
            credit_min=float(os.getenv("CREDIT_MIN", "-1.0")),
            credit_max=float(os.getenv("CREDIT_MAX", "1.0")),
            smoothing_window=int(os.getenv("CREDIT_WINDOW", "300")),
            policy_name=os.getenv("SCHEDULER_POLICY", "credit-greedy"),
            valid_for=int(os.getenv("SCHEDULE_VALID_FOR", "60")),
            discovery_interval=int(os.getenv("STRATEGY_DISCOVERY_INTERVAL", "60")),
        )

    def _load_default_flavours(self) -> List[FlavourProfile]:
        raw = os.getenv("SCHEDULER_STRATEGIES")
        if raw:
            try:
                payload = json.loads(raw)
                strategies = []
                for raw in payload:
                    name = raw.get("name")
                    precision = float(raw.get("precision", 1.0))
                    carbon_intensity = float(raw.get("carbon_intensity", 0.0))
                    if precision > 1.0:
                        precision /= 100.0
                    precision = max(0.0, min(precision, 1.0))
                    strategy_name = str(name) if isinstance(name, str) and name else precision_key(precision)
                    strategies.append(
                        FlavourProfile(
                            name=strategy_name,
                            precision=precision,
                            carbon_intensity=carbon_intensity,
                            enabled=bool(raw.get("enabled", True)),
                        )
                    )
                return strategies
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                _LOGGER.warning("Invalid SCHEDULER_STRATEGIES env var: %s", exc)
        return [
            FlavourProfile(precision_key(1.0), precision=1.0, carbon_intensity=1.0),
            FlavourProfile(precision_key(0.85), precision=0.85, carbon_intensity=0.7),
            FlavourProfile(precision_key(0.7), precision=0.7, carbon_intensity=0.4),
        ]

    def _build_policy(self, name: str) -> SchedulerPolicy:
        builder = _POLICY_BUILDERS.get(name, CreditGreedyPolicy)
        if name not in _POLICY_BUILDERS:
            _LOGGER.warning("Unknown policy '%s', falling back to credit-greedy", name)
        return builder(self.ledger)

    def reload_policy(self, name: str) -> None:
        with self._lock:
            self.policy = self._build_policy(name)
            self.config.policy_name = name

    def refresh_flavours(self, flavours: Iterable[FlavourProfile]) -> None:
        candidate_flavours = list(flavours)
        if candidate_flavours:
            merged = _merge_with_fallback(candidate_flavours, self._fallback_flavours)
        else:
            merged = list(self._fallback_flavours)
        self.registry.replace(merged)

    def evaluate(self) -> ScheduleDecision:
        """Run the scheduler once and produce the next decision."""

        with self._lock:
            flavours = self.registry.list()
            if not flavours:
                raise RuntimeError("No flavours available for scheduling")

            forecast = self.forecast_manager.snapshot()
            result = self.policy.evaluate(flavours, forecast)
            credit_balance = self.ledger.update(result.avg_precision)
            credit_velocity = self.ledger.velocity()
            scaling = ScalingDirective.from_state(
                credit_balance=credit_balance,
                config=self.config,
                forecast=forecast,
                component_bounds=self.component_bounds,
            )
            decision = ScheduleDecision.from_policy(
                result,
                flavours,
                self.config,
                credit_balance,
                credit_velocity,
                scaling,
                forecast,
            )
            self._update_metrics(decision, result, forecast)
            return decision

    def _update_metrics(
        self,
        decision: ScheduleDecision,
        policy_result: PolicyResult,
        forecast: ForecastSnapshot,
    ) -> None:
        for flavour, weight in decision.flavour_weights.items():
            self._metric_flavour.labels(self.namespace, self.name, flavour).set(weight)
        self._metric_valid_until.labels(self.namespace, self.name).set(decision.valid_until.timestamp())

        policy = self.config.policy_name
        self._metric_credit_balance.labels(self.namespace, self.name, policy).set(decision.credits["balance"])
        self._metric_credit_velocity.labels(self.namespace, self.name, policy).set(decision.credits["velocity"])
        self._metric_precision.labels(self.namespace, self.name, policy).set(policy_result.avg_precision)
        self._metric_processing_throttle.labels(self.namespace, self.name, policy).set(decision.scaling.throttle)

        ceilings = decision.scaling.ceilings or {}
        components = set(self.component_bounds.keys()) | set(ceilings.keys())
        for component in components:
            value = float(ceilings.get(component, 0))
            self._metric_ceiling.labels(self.namespace, self.name, component).set(value)

        for strategy, weight in policy_result.weights.items():
            self._metric_policy_choice.labels(self.namespace, self.name, policy, strategy).inc(weight)

        # Export current and next forecasts with legacy labels (no timestamp)
        if forecast.intensity_now is not None:
            self._metric_forecast.labels(self.namespace, self.name, policy, "now").set(forecast.intensity_now)
        if forecast.intensity_next is not None:
            self._metric_forecast.labels(self.namespace, self.name, policy, "next").set(forecast.intensity_next)
        
        # Export extended forecast schedule with explicit timestamps
        if forecast.schedule:
            now = datetime.now(timezone.utc)
            now_ts = now.timestamp()
            
            # Clean up old forecasts (older than 1 hour ago)
            _FORECAST_COLLECTOR.clear_old_forecasts(now_ts - 3600)
            
            for point in forecast.schedule:
                if point.forecast is not None:
                    # Calculate hours ahead from now to the midpoint of the forecast period
                    midpoint = point.start + (point.end - point.start) / 2
                    hours_ahead = (midpoint - now).total_seconds() / 3600.0
                    # Round to 1 decimal for cleaner labels
                    hours_label = f"{hours_ahead:.1f}h"
                    
                    # Set the forecast with the actual target timestamp
                    target_timestamp = midpoint.timestamp()
                    _FORECAST_COLLECTOR.set_forecast(
                        self.namespace, 
                        self.name, 
                        policy, 
                        hours_label, 
                        point.forecast,
                        target_timestamp
                    )
                    
                    # Also keep the old metric for backward compatibility
                    self._metric_forecast.labels(self.namespace, self.name, policy, hours_label).set(point.forecast)

    def publish_manual_schedule(self, schedule: Dict[str, object]) -> None:
        """Expose metrics for a manually provided schedule payload."""

        flavours = schedule.get("flavourWeights", {}) or {}
        valid_until = schedule.get("validUntil")
        processing = schedule.get("processing") or {}

        for flavour, weight in flavours.items():
            try:
                self._metric_flavour.labels(self.namespace, self.name, flavour).set(float(weight))
            except (TypeError, ValueError):
                continue

        if isinstance(valid_until, str):
            try:
                ts = datetime.strptime(valid_until, "%Y-%m-%dT%H:%M:%SZ").timestamp()
                self._metric_valid_until.labels(self.namespace, self.name).set(ts)
            except ValueError:
                pass

        policy = self.config.policy_name
        try:
            throttle_val = float(processing.get("throttle", 1.0))
        except (TypeError, ValueError, AttributeError):
            throttle_val = 1.0
        if throttle_val < 0.0:
            throttle_val = 0.0
        elif throttle_val > 1.0:
            throttle_val = 1.0
        self._metric_processing_throttle.labels(self.namespace, self.name, policy).set(throttle_val)

        ceilings = processing.get("ceilings") if isinstance(processing, dict) else {}
        if isinstance(ceilings, dict):
            for component, raw in ceilings.items():
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                self._metric_ceiling.labels(self.namespace, self.name, component).set(value)
