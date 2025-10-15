"""Scheduling heuristics for the carbon-aware scheduler."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional

from .ledger import CreditLedger
from .models import ForecastSnapshot, PolicyDiagnostics, PolicyResult, StrategyProfile


class SchedulerPolicy(ABC):
    """Abstract policy interface."""

    name: str

    def __init__(self, ledger: CreditLedger) -> None:
        self.ledger = ledger

    @abstractmethod
    def evaluate(
        self,
        strategies: Iterable[StrategyProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """Return a distribution for the next scheduling window."""


class CreditGreedyPolicy(SchedulerPolicy):
    """Spend credit on greener strategies while keeping error in check."""

    name = "credit-greedy"

    def evaluate(
        self,
        strategies: Iterable[StrategyProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        strategies = [s for s in strategies if s.enabled]
        if not strategies:
            raise ValueError("no strategies enabled")

        strategies.sort(key=lambda s: s.precision, reverse=True)
        baseline = strategies[0]

        # Portion of traffic we can spend on non-baseline strategies
        allowance = 0.0
        if self.ledger.credit_max > 0:
            allowance = max(0.0, min(1.0, self.ledger.balance / self.ledger.credit_max))

        weights: Dict[str, float] = {baseline.name: max(0.0, 1.0 - allowance)}
        greener = strategies[1:]
        if greener:
            scores = [self._carbon_score(baseline, s) for s in greener]
            score_sum = sum(scores) or len(scores)
            for s, score in zip(greener, scores):
                weights[s.name] = allowance * (score / score_sum)

        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        avg_precision = sum(
            w * self._precision_of_name(strategies, name) for name, w in weights.items()
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
    def _carbon_score(baseline: StrategyProfile, strategy: StrategyProfile) -> float:
        baseline_intensity = baseline.carbon_intensity or 0.0
        intensity_gain = baseline_intensity - (strategy.carbon_intensity or 0.0)
        error_penalty = max(1e-6, strategy.expected_error())
        score = intensity_gain if intensity_gain > 0 else 0.5 * error_penalty
        return max(1e-6, score / error_penalty)

    @staticmethod
    def _precision_of_name(strategies: List[StrategyProfile], name: str) -> float:
        for s in strategies:
            if s.name == name:
                return s.precision
        return 1.0


class ForecastAwarePolicy(CreditGreedyPolicy):
    """Adjust allowance depending on expected carbon intensity trend."""

    name = "forecast-aware"

    def evaluate(
        self,
        strategies: Iterable[StrategyProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        strategy_list = [s for s in strategies]
        base = super().evaluate(strategy_list[:], forecast)
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
            weights[name] * self._precision_of_name(strategy_list, name)
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
    """Maintain target average precision by tiering strategies."""

    name = "precision-tier"

    def evaluate(
        self,
        strategies: Iterable[StrategyProfile],
    forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        strategies = [s for s in strategies if s.enabled]
        if not strategies:
            raise ValueError("no strategies enabled")

        tiers = {
            "tier-1": [s for s in strategies if s.precision >= 0.95],
            "tier-2": [s for s in strategies if 0.8 <= s.precision < 0.95],
            "tier-3": [s for s in strategies if s.precision < 0.8],
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
            for strategy in tiers[tier] or []:
                weights[strategy.name] = share / total

        if not weights:
            # fallback to single strategy
            best = max(strategies, key=lambda s: s.precision)
            weights[best.name] = 1.0

        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        avg_precision = sum(
            weights[name] * self._precision_of_name(strategies, name) for name in weights
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
    def _precision_of_name(strategies: Iterable[StrategyProfile], name: str) -> float:
        for s in strategies:
            if s.name == name:
                return s.precision
        return 1.0
