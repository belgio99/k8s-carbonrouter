"""Scheduling heuristics (strategies/policies) for the carbon-aware scheduler.

Note on terminology:
- Policy/Strategy (in this file): Scheduling algorithms like credit-greedy, forecast-aware
- Flavour: Precision variants like precision-30, precision-50, precision-100
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional

from .ledger import CreditLedger
from .models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class SchedulerPolicy(ABC):
    """Abstract scheduling policy interface."""

    name: str

    def __init__(self, ledger: CreditLedger) -> None:
        self.ledger = ledger

    @abstractmethod
    def evaluate(
        self,
        flavours: Iterable[FlavourProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """Return a flavour distribution for the next scheduling window."""


class CreditGreedyPolicy(SchedulerPolicy):
    """Spend credit on greener flavours while keeping error in check."""

    name = "credit-greedy"

    def evaluate(
        self,
        flavours: Iterable[FlavourProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        flavours_list.sort(key=lambda f: f.precision, reverse=True)
        baseline = flavours_list[0]

        # Portion of traffic we can spend on non-baseline flavours
        allowance = 0.0
        if self.ledger.credit_max > 0:
            allowance = max(0.0, min(1.0, self.ledger.balance / self.ledger.credit_max))

        weights: Dict[str, float] = {baseline.name: max(0.0, 1.0 - allowance)}
        greener = flavours_list[1:]
        if greener:
            scores = [self._carbon_score(baseline, f) for f in greener]
            score_sum = sum(scores) or len(scores)
            for f, score in zip(greener, scores):
                weights[f.name] = allowance * (score / score_sum)

        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        avg_precision = sum(
            w * self._precision_of_name(flavours_list, name) for name, w in weights.items()
        )
        diagnostics = PolicyDiagnostics(
            {
                "credit_balance": self.ledger.balance,
                "allowance": allowance,
                "avg_precision": avg_precision,
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)

    @staticmethod
    def _carbon_score(baseline: FlavourProfile, flavour: FlavourProfile) -> float:
        baseline_intensity = baseline.carbon_intensity or 0.0
        intensity_gain = baseline_intensity - (flavour.carbon_intensity or 0.0)
        error_penalty = max(1e-6, flavour.expected_error())
        score = intensity_gain if intensity_gain > 0 else 0.5 * error_penalty
        return max(1e-6, score / error_penalty)

    @staticmethod
    def _precision_of_name(flavours: List[FlavourProfile], name: str) -> float:
        for f in flavours:
            if f.name == name:
                return f.precision
        return 1.0


class ForecastAwarePolicy(CreditGreedyPolicy):
    """Adjust allowance depending on expected carbon intensity trend."""

    name = "forecast-aware"

    def evaluate(
        self,
        flavours: Iterable[FlavourProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours]
        base = super().evaluate(flavours_list[:], forecast)
        if not forecast or forecast.intensity_now is None or forecast.intensity_next is None:
            return base

        trend = forecast.intensity_next - forecast.intensity_now
        adjustment = 0.0
        if trend > 0:
            adjustment = -min(0.3, trend / max(forecast.intensity_now, 1e-6) * 0.5)
        elif trend < 0:
            adjustment = min(0.3, abs(trend) / max(forecast.intensity_now, 1e-6) * 0.5)

        weights = {
            name: max(0.0, min(1.0, weight + adjustment if name != max(base.weights, key=base.weights.get) else weight - adjustment))
            for name, weight in base.weights.items()
        }
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        avg_precision = sum(
            weights[name] * self._precision_of_name(flavours_list, name)
            for name in weights
        )
        diagnostics = PolicyDiagnostics(
            {
                **base.diagnostics.fields,
                "trend": trend,
                "adjustment": adjustment,
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)


class PrecisionTierPolicy(SchedulerPolicy):
    """Maintain target average precision by tiering flavours."""

    name = "precision-tier"

    def evaluate(
        self,
        flavours: Iterable[FlavourProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        tiers = {
            "tier-1": [f for f in flavours_list if f.precision >= 0.95],
            "tier-2": [f for f in flavours_list if 0.8 <= f.precision < 0.95],
            "tier-3": [f for f in flavours_list if f.precision < 0.8],
        }

        # Base allocations respecting ledger balance
        allowance = 0.0
        if self.ledger.credit_max > 0:
            allowance = max(0.0, min(1.0, self.ledger.balance / self.ledger.credit_max))

        weights: Dict[str, float] = {}
        total_primary = len(tiers["tier-1"]) or 1
        total_secondary = len(tiers["tier-2"]) or 1
        total_third = len(tiers["tier-3"]) or 1

        primary_share = max(0.3, 1.0 - allowance)
        secondary_share = min(0.5, allowance * 0.6)
        tertiary_share = max(0.0, allowance - secondary_share)

        for tier, share, total in (
            ("tier-1", primary_share, total_primary),
            ("tier-2", secondary_share, total_secondary),
            ("tier-3", tertiary_share, total_third),
        ):
            for flavour in tiers[tier] or []:
                weights[flavour.name] = share / total

        if not weights:
            # fallback to single flavour
            best = max(flavours_list, key=lambda f: f.precision)
            weights[best.name] = 1.0

        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        avg_precision = sum(
            weights[name] * self._precision_of_name(flavours_list, name) for name in weights
        )
        diagnostics = PolicyDiagnostics(
            {
                "allowance": allowance,
                "tier_1_share": primary_share,
                "tier_2_share": secondary_share,
                "tier_3_share": tertiary_share,
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)

    @staticmethod
    def _precision_of_name(flavours: Iterable[FlavourProfile], name: str) -> float:
        for f in flavours:
            if f.name == name:
                return f.precision
        return 1.0
