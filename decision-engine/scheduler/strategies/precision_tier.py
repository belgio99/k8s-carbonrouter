"""Precision-Tier scheduling strategy.

Maintains target average precision by tiering flavours.
"""

from __future__ import annotations

from typing import Dict, Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class PrecisionTierPolicy(SchedulerPolicy):
    """Maintain target average precision by tiering flavours."""

    name = "precision-tier"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
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
            allowance = max(0.0, min(1.0, 0.5 - self.ledger.balance / (2 * self.ledger.credit_max)))

        tier_shares = {
            "tier-1": max(0.25, 1.0 - allowance),
            "tier-2": allowance * 0.45,
            "tier-3": allowance * 0.55,
        }

        # Bias tier shares by the average carbon intensity of each tier.
        tier_carbon_scores = {
            tier: self._tier_carbon_factor(bucket)
            for tier, bucket in tiers.items()
        }
        for tier in tier_shares:
            tier_shares[tier] *= tier_carbon_scores[tier] or 0.0

        total_share = sum(tier_shares.values()) or 1.0
        tier_shares = {k: v / total_share for k, v in tier_shares.items()}

        weights: Dict[str, float] = {}
        for tier, bucket in tiers.items():
            share = tier_shares.get(tier, 0.0)
            if not bucket or share <= 0:
                continue
            flavour_scores = [self._flavour_carbon_weight(f) for f in bucket]
            score_sum = sum(flavour_scores) or len(bucket)
            for flavour, score in zip(bucket, flavour_scores):
                weights[flavour.name] = share * (score / score_sum)

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
                "tier_1_share": tier_shares.get("tier-1"),
                "tier_2_share": tier_shares.get("tier-2"),
                "tier_3_share": tier_shares.get("tier-3"),
            }
        )
        return PolicyResult(weights, avg_precision, diagnostics)

    @staticmethod
    def _precision_of_name(flavours: list[FlavourProfile], name: str) -> float:
        for f in flavours:
            if f.name == name:
                return f.precision
        return 1.0

    @staticmethod
    def _tier_carbon_factor(flavours: list[FlavourProfile]) -> float:
        if not flavours:
            return 0.0
        baseline = 150.0
        avg = sum((f.carbon_intensity or baseline) for f in flavours) / len(flavours)
        return baseline / max(25.0, avg)

    @staticmethod
    def _flavour_carbon_weight(flavour: FlavourProfile) -> float:
        carbon = flavour.carbon_intensity or 150.0
        precision_bias = 0.5 + 0.5 * flavour.precision
        return max(1e-3, precision_bias * (150.0 / max(25.0, carbon)))
