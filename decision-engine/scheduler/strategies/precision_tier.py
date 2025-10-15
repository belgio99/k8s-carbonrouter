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
    def _precision_of_name(flavours: list[FlavourProfile], name: str) -> float:
        for f in flavours:
            if f.name == name:
                return f.precision
        return 1.0
