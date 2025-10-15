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
