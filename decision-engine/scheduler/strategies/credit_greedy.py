"""Credit-Greedy scheduling strategy.

Spends credit on greener flavours while keeping error in check.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class CreditGreedyPolicy(SchedulerPolicy):
    """Spend credit on greener flavours while keeping error in check."""

    name = "credit-greedy"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        flavours_list.sort(key=lambda f: f.precision, reverse=True)
        baseline = flavours_list[0]

        # Portion of traffic we can spend on non-baseline flavours is first dictated by credit.
        credit_span = (self.ledger.credit_max - self.ledger.credit_min) or 1.0
        normalised_credit = (self.ledger.balance - self.ledger.credit_min) / credit_span
        base_allowance = max(0.0, min(1.0, 1.0 - normalised_credit))

        # Guard-rail: if we are already in quality debt (positive balance), shrink allowance
        if self.ledger.balance > 0.0 and self.ledger.credit_max > 0:
            debt_ratio = min(1.0, self.ledger.balance / self.ledger.credit_max)
            base_allowance *= max(0.2, 1.0 - 0.5 * debt_ratio)

        # React to the current (not forecasted) carbon intensity.
        carbon_multiplier = 1.0
        carbon_ratio = None
        if forecast and forecast.intensity_now is not None:
            carbon_now = forecast.intensity_now
            low_carbon = 80.0
            high_carbon = 280.0
            span = high_carbon - low_carbon
            carbon_ratio = (carbon_now - low_carbon) / span if span > 0 else 0.0
            carbon_ratio = max(0.0, min(1.0, carbon_ratio))
            # High carbon → allow more low-precision traffic, low carbon → stay conservative
            carbon_multiplier = 0.6 + 0.8 * carbon_ratio

        allowance = max(0.0, min(0.95, base_allowance * carbon_multiplier))

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
                "base_allowance": base_allowance,
                "carbon_multiplier": carbon_multiplier,
                "allowance": allowance,
                "avg_precision": avg_precision,
                "carbon_now": forecast.intensity_now if forecast else None,
                "normalised_credit": normalised_credit,
                "carbon_ratio": carbon_ratio,
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)

    @staticmethod
    def _carbon_score(baseline: FlavourProfile, flavour: FlavourProfile) -> float:
        baseline_intensity = baseline.carbon_intensity or 0.0
        intensity_gain = baseline_intensity - (flavour.carbon_intensity or 0.0)
        error_penalty = max(1e-6, flavour.expected_error())
        # Only greener flavours get meaningful scores
        score = max(1e-6, intensity_gain) if intensity_gain > 0 else 1e-6
        return max(1e-6, score / error_penalty)

    @staticmethod
    def _precision_of_name(flavours: List[FlavourProfile], name: str) -> float:
        for f in flavours:
            if f.name == name:
                return f.precision
        return 1.0
