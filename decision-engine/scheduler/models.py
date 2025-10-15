"""Data models used by the carbon-aware scheduler."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def precision_key(precision: float) -> str:
    """Return a stable name for a precision ratio."""

    clamped = _clamp(precision, 0.0, 1.0)
    return f"precision-{int(round(clamped * 100))}"


@dataclass
class StrategyProfile:
    """Represents a runnable strategy variant for the target service."""

    name: str
    precision: float = 1.0  # relative to baseline strategy
    carbon_intensity: float = 0.0  # gCO2eq per request (relative delta)
    enabled: bool = True
    annotations: Mapping[str, str] = field(default_factory=dict)

    def expected_error(self) -> float:
        """Return the expected relative error contributed by this strategy."""

        return max(0.0, 1.0 - self.precision)


@dataclass
class ForecastPoint:
    """Carbon forecast for a time interval."""

    start: datetime
    end: datetime
    forecast: Optional[float] = None
    index: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "from": self.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": self.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forecast": self.forecast,
            "index": self.index,
        }


@dataclass
class ForecastSnapshot:
    """Carbon intensity and demand forecasts for the next time window."""

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
    """Runtime configuration knobs loaded from the environment."""

    target_error: float = 0.05
    credit_min: float = -0.5
    credit_max: float = 0.5
    smoothing_window: int = 300  # seconds
    policy_name: str = "credit-greedy"
    valid_for: int = 60  # seconds per schedule publication
    discovery_interval: int = 60  # seconds between strategy refreshes
    carbon_target: str = "national"
    carbon_timeout: float = 2.0
    carbon_cache_ttl: float = 300.0

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        return cls(
            target_error=float(os.getenv("TARGET_ERROR", "0.05")),
            credit_min=float(os.getenv("CREDIT_MIN", "-0.5")),
            credit_max=float(os.getenv("CREDIT_MAX", "0.5")),
            smoothing_window=int(os.getenv("CREDIT_WINDOW", "300")),
            policy_name=os.getenv("SCHEDULER_POLICY", "credit-greedy"),
            valid_for=int(os.getenv("SCHEDULE_VALID_FOR", "60")),
            discovery_interval=int(os.getenv("STRATEGY_DISCOVERY_INTERVAL", "60")),
            carbon_target=os.getenv("CARBON_API_TARGET", "national"),
            carbon_timeout=float(os.getenv("CARBON_API_TIMEOUT", "2.0")),
            carbon_cache_ttl=float(os.getenv("CARBON_API_CACHE_TTL", "300.0")),
        )

    def clone(self) -> "SchedulerConfig":
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
    """Structured diagnostics exposed alongside the published schedule."""

    fields: Dict[str, float] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Outcome of a policy evaluation."""

    weights: Dict[str, float]
    avg_precision: float
    diagnostics: PolicyDiagnostics


@dataclass
class ScalingDirective:
    """Processing throttle recommendations for downstream autoscaling."""

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
        component_bounds: Mapping[str, Mapping[str, int]] | None = None,
    ) -> "ScalingDirective":
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
    """Final schedule shared with the router and CRD status."""

    flavour_weights: Dict[str, int]
    flavour_rules: List[Dict[str, object]]
    strategies: List[Dict[str, object]]
    valid_until: datetime
    credits: Dict[str, float]
    policy_name: str
    diagnostics: Dict[str, float]
    avg_precision: float
    scaling: ScalingDirective

    def as_dict(self) -> Dict[str, object]:
        """Return a serialisable representation expected by existing consumers."""

        return {
            "flavourWeights": self.flavour_weights,
            "flavourRules": self.flavour_rules,
            "strategies": self.strategies,
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
        strategies: List[StrategyProfile],
        config: SchedulerConfig,
        credit_balance: float,
        credit_velocity: float,
        scaling: ScalingDirective,
    ) -> "ScheduleDecision":
        """Assemble a schedule decision from policy output."""

        valid_until = datetime.utcnow() + timedelta(seconds=config.valid_for)

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

        flavour_rules: List[Dict[str, object]] = []
        strategies_meta: List[Dict[str, object]] = []
        for strategy in strategies:
            weight = scaled.get(strategy.name, 0)
            precision_pct = int(round(strategy.precision * 100))
            flavour_rules.append(
                {
                    "flavourName": strategy.name,
                    "precision": precision_pct,
                    "weight": weight,
                }
            )
            strategies_meta.append(
                {
                    "name": strategy.name,
                    "precision": precision_pct,
                    "weight": weight,
                    "carbonIntensity": strategy.carbon_intensity,
                    "enabled": strategy.enabled,
                }
            )

        return cls(
            flavour_weights=scaled,
            flavour_rules=flavour_rules,
            strategies=strategies_meta,
            valid_until=valid_until,
            credits=credit_stats,
            policy_name=config.policy_name,
            diagnostics=policy_result.diagnostics.fields,
            avg_precision=policy_result.avg_precision,
            scaling=scaling,
        )
