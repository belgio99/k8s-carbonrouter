"""Scheduler engine orchestrating policies, ledger, and forecasts."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional

from prometheus_client import Counter, Gauge

from .ledger import CreditLedger
from .models import (
    ForecastSnapshot,
    PolicyResult,
    ScheduleDecision,
    SchedulerConfig,
    ScalingDirective,
    StrategyProfile,
    precision_key,
)
from .policies import CreditGreedyPolicy, ForecastAwarePolicy, PrecisionTierPolicy, SchedulerPolicy
from .providers import CarbonForecastProvider, DemandEstimator, ForecastManager

_LOGGER = logging.getLogger("scheduler")


class StrategyRegistry:
    """In-memory registry of available strategies."""

    def __init__(self, strategies: Optional[Iterable[StrategyProfile]] = None) -> None:
        self._lock = threading.Lock()
        self._strategies: Dict[str, StrategyProfile] = {}
        if strategies:
            for strategy in strategies:
                self._strategies[strategy.name] = strategy

    def list(self) -> List[StrategyProfile]:
        with self._lock:
            return list(self._strategies.values())

    def replace(self, strategies: Iterable[StrategyProfile]) -> None:
        with self._lock:
            self._strategies = {s.name: s for s in strategies}

    def upsert(self, strategy: StrategyProfile) -> None:
        with self._lock:
            self._strategies[strategy.name] = strategy


_POLICY_BUILDERS: Dict[str, type[SchedulerPolicy]] = {
    "credit-greedy": CreditGreedyPolicy,
    "forecast-aware": ForecastAwarePolicy,
    "precision-tier": PrecisionTierPolicy,
}

# Global Prometheus metrics reused across scheduler sessions to avoid duplicate registrations.
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


def _merge_with_fallback(
    primary: Iterable[StrategyProfile],
    fallback: Iterable[StrategyProfile],
) -> List[StrategyProfile]:
    merged: Dict[str, StrategyProfile] = {strategy.name: strategy for strategy in fallback}
    for strategy in primary:
        merged[strategy.name] = strategy
    return sorted(merged.values(), key=lambda item: item.precision, reverse=True)


class SchedulerEngine:
    """High-level orchestrator for the credit-based scheduler."""

    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
        namespace: str = "default",
        name: str = "default",
        component_bounds: Optional[Mapping[str, Mapping[str, int]]] = None,
        strategies: Optional[Iterable[StrategyProfile]] = None,
    ) -> None:
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
        default_strategies = self._load_default_strategies()
        provided_strategies = list(strategies) if strategies else []
        # Use only provided strategies if available, otherwise use defaults
        initial_strategies = provided_strategies if provided_strategies else default_strategies
        self._fallback_strategies = list(default_strategies)
        self.registry = StrategyRegistry(initial_strategies)
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
            target_error=float(os.getenv("TARGET_ERROR", "0.05")),
            credit_min=float(os.getenv("CREDIT_MIN", "-0.5")),
            credit_max=float(os.getenv("CREDIT_MAX", "0.5")),
            smoothing_window=int(os.getenv("CREDIT_WINDOW", "300")),
            policy_name=os.getenv("SCHEDULER_POLICY", "credit-greedy"),
            valid_for=int(os.getenv("SCHEDULE_VALID_FOR", "60")),
            discovery_interval=int(os.getenv("STRATEGY_DISCOVERY_INTERVAL", "60")),
        )

    def _load_default_strategies(self) -> List[StrategyProfile]:
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
                        StrategyProfile(
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
            StrategyProfile(precision_key(1.0), precision=1.0, carbon_intensity=1.0),
            StrategyProfile(precision_key(0.85), precision=0.85, carbon_intensity=0.7),
            StrategyProfile(precision_key(0.7), precision=0.7, carbon_intensity=0.4),
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

    def refresh_strategies(self, strategies: Iterable[StrategyProfile]) -> None:
        candidate_strategies = list(strategies)
        if candidate_strategies:
            merged = _merge_with_fallback(candidate_strategies, self._fallback_strategies)
        else:
            merged = list(self._fallback_strategies)
        self.registry.replace(merged)

    def evaluate(self) -> ScheduleDecision:
        """Run the scheduler once and produce the next decision."""

        with self._lock:
            strategies = self.registry.list()
            if not strategies:
                raise RuntimeError("No strategies available for scheduling")

            forecast = self.forecast_manager.snapshot()
            result = self.policy.evaluate(strategies, forecast)
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
                strategies,
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

        if forecast.intensity_now is not None:
            self._metric_forecast.labels(self.namespace, self.name, policy, "now").set(forecast.intensity_now)
        if forecast.intensity_next is not None:
            self._metric_forecast.labels(self.namespace, self.name, policy, "next").set(forecast.intensity_next)

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
