"""
Data Models for Carbon-Aware Scheduler

This module defines the core data structures used throughout the scheduler:
- FlavourProfile: Precision/quality variants (e.g., low, medium, high power)
- ForecastPoint/Snapshot: Carbon intensity and demand forecasts
- SchedulerConfig: Runtime configuration parameters
- ScheduleDecision: Final traffic distribution schedule
- PolicyResult: Output from scheduling policies
- ScalingDirective: Autoscaling recommendations
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp value to the range [low, high]."""
    return max(low, min(value, high))


def precision_key(precision: float) -> str:
    """
    Generate a standard strategy name from precision value.
    
    Args:
        precision: Precision ratio (0.0-1.0)
        
    Returns:
        Strategy name like "precision-30" for 0.3
    """
    clamped = _clamp(precision, 0.0, 1.0)
    return f"precision-{int(round(clamped * 100))}"


@dataclass
class FlavourProfile:
    """
    Represents a precision/quality variant (flavour) for the target service.
    
    Each flavour corresponds to a deployment with a specific precision level
    (e.g., low-power model at 30% precision, high-power at 100% precision).
    
    Attributes:
        name: Flavour identifier (e.g., "precision-30")
        precision: Quality level relative to baseline (0.0-1.0)
        carbon_intensity: Estimated carbon cost per request (gCO2eq)
        enabled: Whether this flavour is currently available
        annotations: Metadata from Kubernetes deployment labels
    """

    name: str
    precision: float = 1.0  # Quality level (0.0-1.0)
    carbon_intensity: float = 0.0  # gCO2eq per request
    enabled: bool = True
    annotations: Mapping[str, str] = field(default_factory=dict)

    def expected_error(self) -> float:
        """
        Calculate the expected quality error for this flavour.
        
        Returns:
            Error ratio (0.0 = perfect, 1.0 = worst)
        """
        return max(0.0, 1.0 - self.precision)


@dataclass
class ForecastPoint:
    """
    Carbon intensity forecast for a specific time interval.
    
    Attributes:
        start: Beginning of forecast period
        end: End of forecast period
        forecast: Predicted carbon intensity (gCO2eq/kWh)
        index: Carbon intensity index/category (e.g., "low", "medium", "high")
    """

    start: datetime
    end: datetime
    forecast: Optional[float] = None
    index: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        """Serialize forecast point to dictionary."""
        return {
            "from": self.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": self.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forecast": self.forecast,
            "index": self.index,
        }


@dataclass
class ForecastSnapshot:
    """
    Snapshot of carbon intensity and demand forecasts.
    
    Contains current and next-period forecasts used for scheduling decisions.
    The schedule field provides a longer-term forecast for planning.
    
    Attributes:
        intensity_now: Current carbon intensity (gCO2eq/kWh)
        intensity_next: Next period carbon intensity
        index_now: Current carbon intensity category
        index_next: Next period carbon intensity category
        demand_now: Current workload demand estimate
        demand_next: Next period demand estimate
        generated_at: Timestamp when forecast was generated
        schedule: Extended forecast schedule for future periods
    """

    intensity_now: Optional[float] = None
    intensity_next: Optional[float] = None
    index_now: Optional[str] = None
    index_next: Optional[str] = None
    demand_now: Optional[float] = None
    demand_next: Optional[float] = None
    generated_at: datetime = field(default_factory=datetime.utcnow)
    schedule: List[ForecastPoint] = field(default_factory=list)


@dataclass
class SchedulerConfig:
    """
    Runtime configuration for the scheduler.
    
    Loaded from environment variables with sensible defaults.
    Can be overridden via API for specific TrafficSchedules.
    
    Attributes:
        target_error: Target quality error threshold (0.0-1.0, default 0.1 = 10% error)
        credit_min: Minimum credit balance (quality debt limit, range: -1.0 to 0.0)
        credit_max: Maximum credit balance (quality surplus limit, range: 0.0 to 1.0)
        smoothing_window: Time window for credit velocity smoothing (seconds)
        policy_name: Scheduling policy to use (e.g., "credit-greedy", "forecast-aware")
        valid_for: Schedule validity period (seconds)
        discovery_interval: How often to refresh strategy list (seconds)
        carbon_target: Carbon API target region (e.g., "national", "local")
        carbon_timeout: Timeout for carbon API requests (seconds)
        carbon_cache_ttl: Cache TTL for carbon data (seconds)
    """

    target_error: float = 0.1
    credit_min: float = -1.0
    credit_max: float = 1.0
    smoothing_window: int = 300  # seconds
    policy_name: str = "credit-greedy"
    valid_for: int = 60  # seconds per schedule publication
    discovery_interval: int = 60  # seconds between strategy refreshes
    carbon_target: str = "national"
    carbon_timeout: float = 2.0
    carbon_cache_ttl: float = 300.0

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        """
        Load configuration from environment variables.
        
        Returns:
            SchedulerConfig instance with values from environment
        """
        return cls(
            target_error=float(os.getenv("TARGET_ERROR", "0.1")),
            credit_min=float(os.getenv("CREDIT_MIN", "-1.0")),
            credit_max=float(os.getenv("CREDIT_MAX", "1.0")),
            smoothing_window=int(os.getenv("CREDIT_WINDOW", "300")),
            policy_name=os.getenv("SCHEDULER_POLICY", "credit-greedy"),
            valid_for=int(os.getenv("SCHEDULE_VALID_FOR", "60")),
            discovery_interval=int(os.getenv("STRATEGY_DISCOVERY_INTERVAL", "60")),
            carbon_target=os.getenv("CARBON_API_TARGET", "national"),
            carbon_timeout=float(os.getenv("CARBON_API_TIMEOUT", "2.0")),
            carbon_cache_ttl=float(os.getenv("CARBON_API_CACHE_TTL", "300.0")),
        )

    def clone(self) -> "SchedulerConfig":
        """Create a deep copy of this configuration."""
        return SchedulerConfig(
            target_error=self.target_error,
            credit_min=self.credit_min,
            credit_max=self.credit_max,
            smoothing_window=self.smoothing_window,
            policy_name=self.policy_name,
            valid_for=self.valid_for,
            discovery_interval=self.discovery_interval,
            carbon_target=self.carbon_target,
            carbon_timeout=self.carbon_timeout,
            carbon_cache_ttl=self.carbon_cache_ttl,
        )

    def apply_overrides(self, overrides: Mapping[str, object]) -> None:
        """
        Apply configuration overrides in-place.
        
        Args:
            overrides: Dictionary of configuration keys and values to override
        """
        if not overrides:
            return
        if "targetError" in overrides and overrides["targetError"] is not None:
            self.target_error = float(overrides["targetError"])
        if "creditMin" in overrides and overrides["creditMin"] is not None:
            self.credit_min = float(overrides["creditMin"])
        if "creditMax" in overrides and overrides["creditMax"] is not None:
            self.credit_max = float(overrides["creditMax"])
        if "creditWindow" in overrides and overrides["creditWindow"] is not None:
            self.smoothing_window = int(overrides["creditWindow"])
        if "policy" in overrides and overrides["policy"]:
            self.policy_name = str(overrides["policy"])
        if "validFor" in overrides and overrides["validFor"] is not None:
            self.valid_for = int(overrides["validFor"])
        if "discoveryInterval" in overrides and overrides["discoveryInterval"] is not None:
            self.discovery_interval = int(overrides["discoveryInterval"])
        if "carbonTarget" in overrides and overrides["carbonTarget"]:
            self.carbon_target = str(overrides["carbonTarget"])
        if "carbonTimeout" in overrides and overrides["carbonTimeout"] is not None:
            self.carbon_timeout = float(overrides["carbonTimeout"])
        if "carbonCacheTTL" in overrides and overrides["carbonCacheTTL"] is not None:
            self.carbon_cache_ttl = float(overrides["carbonCacheTTL"])

    def as_dict(self) -> Dict[str, object]:
        return {
            "targetError": self.target_error,
            "creditMin": self.credit_min,
            "creditMax": self.credit_max,
            "creditWindow": self.smoothing_window,
            "policy": self.policy_name,
            "validFor": self.valid_for,
            "discoveryInterval": self.discovery_interval,
            "carbonTarget": self.carbon_target,
            "carbonTimeout": self.carbon_timeout,
            "carbonCacheTTL": self.carbon_cache_ttl,
        }


@dataclass
class PolicyDiagnostics:
    """
    Diagnostic information from policy evaluation.
    
    Exposes internal policy state for debugging and monitoring.
    """

    fields: Dict[str, float] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """
    Result of a scheduling policy evaluation.
    
    Attributes:
        weights: Traffic distribution weights by strategy name (0.0-1.0)
        avg_precision: Weighted average precision of the schedule
        diagnostics: Policy-specific diagnostic data
    """

    weights: Dict[str, float]
    avg_precision: float
    diagnostics: PolicyDiagnostics


@dataclass
class ScalingDirective:
    """
    Autoscaling recommendations based on carbon intensity and quality credits.
    
    Provides throttling signals to KEDA/HPA for adaptive resource scaling.
    
    Attributes:
        throttle: Overall processing throttle (0.0-1.0)
        credits_ratio: Credit-based scaling factor (0.0-1.0)
        intensity_ratio: Carbon intensity-based scaling factor (0.0-1.0)
        ceilings: Maximum replica counts per component
    """

    throttle: float
    credits_ratio: float
    intensity_ratio: float
    ceilings: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, float]:
        return {
            "throttle": self.throttle,
            "creditsRatio": self.credits_ratio,
            "intensityRatio": self.intensity_ratio,
            "ceilings": self.ceilings,
        }

    @classmethod
    def from_state(
        cls,
        credit_balance: float,
        config: SchedulerConfig,
        forecast: ForecastSnapshot,
        min_throttle: float = 0.2,
        intensity_floor: float = 150.0,
        intensity_ceiling: float = 350.0,
        component_bounds: Optional[Mapping[str, Mapping[str, int]]] = None,
    ) -> "ScalingDirective":
        """
        Compute scaling directive from current system state.
        
        Combines credit balance and carbon intensity to determine throttling level.
        Lower credits or higher carbon intensity results in more aggressive throttling.
        
        Args:
            credit_balance: Current quality credit balance
            config: Scheduler configuration
            forecast: Carbon intensity forecast
            min_throttle: Minimum throttle value (prevents over-throttling)
            intensity_floor: Carbon intensity floor for scaling (gCO2eq/kWh)
            intensity_ceiling: Carbon intensity ceiling for scaling (gCO2eq/kWh)
            component_bounds: Min/max replica constraints per component
            
        Returns:
            ScalingDirective with computed throttle and replica ceilings
        """
        span = config.credit_max - config.credit_min
        if span <= 0:
            credits_ratio = 1.0
        else:
            credits_ratio = _clamp(
                (credit_balance - config.credit_min) / span,
                0.0,
                1.0,
            )

        intensities = [
            value
            for value in (forecast.intensity_now, forecast.intensity_next)
            if value is not None
        ]
        if intensities and intensity_ceiling > intensity_floor:
            peak_intensity = max(intensities)
            norm = (intensity_ceiling - peak_intensity) / (intensity_ceiling - intensity_floor)
            intensity_ratio = _clamp(norm, 0.0, 1.0)
        else:
            intensity_ratio = 1.0

        throttle = _clamp(min(credits_ratio, intensity_ratio), min_throttle, 1.0)

        # Compute replica ceilings for carbon-aware autoscaling.
        # During high carbon periods or low credit balance, reduce maxReplicas to throttle
        # processing. This trades increased latency (queue backpressure) for reduced energy
        # consumption by running fewer replicas. The operator applies these ceilings to
        # KEDA ScaledObjects dynamically.
        ceilings: Dict[str, int] = {}
        if component_bounds:
            for component, bounds in component_bounds.items():
                max_rep = bounds.get("max")
                min_rep = bounds.get("min")
                if max_rep is None:
                    continue
                try:
                    scaled = int(round(max_rep * throttle))
                except TypeError:
                    continue
                if min_rep is not None:
                    scaled = max(scaled, min_rep)
                scaled = max(0, scaled)
                if max_rep is not None:
                    scaled = min(scaled, max_rep)
                ceilings[component] = scaled

        return cls(
            throttle=throttle,
            credits_ratio=credits_ratio,
            intensity_ratio=intensity_ratio,
            ceilings=ceilings,
        )


@dataclass
class ScheduleDecision:
    """
    Final scheduling decision to be published.
    
    Contains the complete traffic distribution schedule with metadata,
    diagnostics, and autoscaling directives. This is the primary output
    of the scheduler consumed by the router and Kubernetes operator.
    
    Attributes:
        flavour_weights: Traffic weights by flavour (0-100, sum to 100)
        flavours: Metadata for each precision flavour (includes precision, weight, name, etc.)
        valid_until: Schedule expiration timestamp
        credits: Quality credit ledger state
        policy_name: Name of scheduling policy/strategy (e.g., "forecast-aware", "credit-greedy")
        diagnostics: Policy-specific diagnostic values
        avg_precision: Weighted average precision of the schedule
        scaling: Autoscaling recommendations
    """

    flavour_weights: Dict[str, int]
    flavours: List[Dict[str, object]]
    valid_until: datetime
    credits: Dict[str, float]
    policy_name: str
    diagnostics: Dict[str, float]
    avg_precision: float
    scaling: ScalingDirective

    def as_dict(self) -> Dict[str, object]:
        """
        Serialize schedule to dictionary for JSON API response.
        
        Returns:
            Dictionary with all schedule fields in API format
        """

        return {
            "flavourWeights": self.flavour_weights,
            "flavours": self.flavours,
            "validUntil": self.valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "credits": self.credits,
            "policy": {"name": self.policy_name},
            "diagnostics": self.diagnostics,
            "avgPrecision": self.avg_precision,
            "processing": self.scaling.as_dict(),
        }

    @classmethod
    def from_policy(
        cls,
        policy_result: PolicyResult,
        flavours: List[FlavourProfile],
        config: SchedulerConfig,
        credit_balance: float,
        credit_velocity: float,
        scaling: ScalingDirective,
        forecast: ForecastSnapshot,
    ) -> "ScheduleDecision":
        """
        Construct a complete schedule decision from policy output.
        
        Normalizes policy weights to integer percentages (0-100), computes
        validity period, and packages all metadata for API consumers.
        
        Args:
            policy_result: Raw output from scheduling policy
            flavours: Available precision flavours
            config: Scheduler configuration
            credit_balance: Current quality credit balance
            credit_velocity: Rate of credit change
            scaling: Autoscaling directives
            forecast: Carbon intensity forecast (used for validity period)
            
        Returns:
            Complete ScheduleDecision ready for publication
        """

        valid_until = datetime.utcnow() + timedelta(seconds=config.valid_for)
        now_utc = datetime.utcnow()
        for point in forecast.schedule:
            candidate = point.end
            if candidate is None:
                continue
            if candidate.tzinfo is not None:
                candidate = candidate.astimezone(timezone.utc).replace(tzinfo=None)
            if candidate <= now_utc:
                continue
            valid_until = candidate
            break

        # Normalise weights to integer percentages summing to 100.
        raw_weights = policy_result.weights
        total = sum(raw_weights.values()) or 1.0
        scaled = {k: int(round((v / total) * 100)) for k, v in raw_weights.items()}
        # Adjust rounding error.
        diff = 100 - sum(scaled.values())
        if diff != 0 and scaled:
            key = max(scaled, key=scaled.get)
            scaled[key] += diff

        credit_stats = {
            "balance": credit_balance,
            "velocity": credit_velocity,
            "target": config.target_error,
            "min": config.credit_min,
            "max": config.credit_max,
        }
        if "allowance" in policy_result.diagnostics.fields:
            credit_stats["allowance"] = policy_result.diagnostics.fields["allowance"]

        flavours_meta: List[Dict[str, object]] = []
        for flavour in flavours:
            weight = scaled.get(flavour.name, 0)
            precision_pct = int(round(flavour.precision * 100))
            flavours_meta.append(
                {
                    "name": flavour.name,
                    "precision": precision_pct,
                    "weight": weight,
                    "carbonIntensity": flavour.carbon_intensity,
                    "enabled": flavour.enabled,
                }
            )

        return cls(
            flavour_weights=scaled,
            flavours=flavours_meta,
            valid_until=valid_until,
            credits=credit_stats,
            policy_name=config.policy_name,
            diagnostics=policy_result.diagnostics.fields,
            avg_precision=policy_result.avg_precision,
            scaling=scaling,
        )
