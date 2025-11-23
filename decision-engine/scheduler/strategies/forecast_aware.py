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
        
        # DAMPING: Ignore small trends to avoid oscillation from noise
        if abs(trend) < 15.0:
            return base

        adjustment = 0.0
        # DAMPING: Reduced sensitivity (0.25x) and lower cap (0.15) to prevent oscillation
        if trend > 0:  # Carbon RISING → SPEND quality NOW before it gets worse (increase p100)
            adjustment = -min(0.15, trend / max(forecast.intensity_now, 1e-6) * 0.25)  # NEGATIVE
        elif trend < 0:  # Carbon FALLING → SAVE quality for cleaner future (decrease p100)
            adjustment = min(0.15, abs(trend) / max(forecast.intensity_now, 1e-6) * 0.25)  # POSITIVE

        # Identify baseline (highest precision) flavour, not highest-weighted flavour
        sorted_flavours = sorted(flavours_list, key=lambda f: f.precision, reverse=True)
        baseline_name = sorted_flavours[0].name if sorted_flavours else None

        weights = dict(base.weights)
        if baseline_name is not None and len(weights) > 1:
            baseline_weight = weights.get(baseline_name, 0.0)
            non_baseline = 1.0 - baseline_weight
            if adjustment > 0 and non_baseline > 0:
                shift = min(adjustment, baseline_weight)
                baseline_weight = max(0.05, baseline_weight - shift)
                weights[baseline_name] = baseline_weight
                scale = (non_baseline + shift) / non_baseline
                for name in weights:
                    if name == baseline_name:
                        continue
                    weights[name] = min(0.95, weights[name] * scale)
            elif adjustment < 0:
                reclaimable = non_baseline
                shift = min(abs(adjustment), reclaimable)
                for name in weights:
                    if name == baseline_name:
                        continue
                    portion = weights[name] / reclaimable if reclaimable else 0.0
                    weights[name] = max(0.02, weights[name] - shift * portion)
                weights[baseline_name] = min(0.98, baseline_weight + shift)

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
                "baseline_weight": weights.get(baseline_name) if baseline_name else None,
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)
