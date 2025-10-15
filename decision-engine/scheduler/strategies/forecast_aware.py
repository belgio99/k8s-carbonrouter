"""Forecast-Aware scheduling strategy.

Adjusts allowance depending on expected carbon intensity trend.
"""

from __future__ import annotations

from typing import Optional

from .credit_greedy import CreditGreedyPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class ForecastAwarePolicy(CreditGreedyPolicy):
    """Adjust allowance depending on expected carbon intensity trend."""

    name = "forecast-aware"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
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
