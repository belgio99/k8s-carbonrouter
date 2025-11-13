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
        # Adjust based on current carbon intensity if forecast available
        # Negative balance (quality surplus from high-precision use) → can use more low-precision
        # Positive balance (quality debt from low-precision use) → must use more high-precision
        base_allowance = 0.0
        if self.ledger.credit_max > 0:
            # Invert balance: negative balance increases allowance, positive decreases it
            base_allowance = max(0.0, min(1.0, 0.5 - self.ledger.balance / (2 * self.ledger.credit_max)))
        
        # Carbon-aware adjustment: when carbon is high, spend credits more aggressively
        carbon_multiplier = 1.0
        if forecast and forecast.intensity_now is not None:
            # Use a baseline carbon intensity matching typical test ranges (40-300 gCO2/kWh)
            # 150 gCO2/kWh allows low carbon (<100) to favor p100, high carbon (>200) to penalize it
            baseline_carbon = 150.0
            carbon_ratio = forecast.intensity_now / baseline_carbon
            # If carbon is high (>150), increase allowance to use more low-precision (low-carbon) flavours
            # If carbon is low (<150), decrease allowance to preserve high precision when carbon is cheap
            carbon_multiplier = max(0.5, min(2.0, carbon_ratio))
        
        allowance = min(1.0, base_allowance * carbon_multiplier)

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
