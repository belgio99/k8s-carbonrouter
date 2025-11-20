"""Random scheduling strategy.

Uses random weights for each flavour at every evaluation.
This is a baseline strategy that doesn't consider carbon intensity or precision.
"""

from __future__ import annotations

import random
from typing import Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class RandomPolicy(SchedulerPolicy):
    """Use random weights at every push."""

    name = "random"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        raw_weights = [random.random() for _ in flavours_list]
        total = sum(raw_weights)
        weights = {f.name: w / total for f, w in zip(flavours_list, raw_weights)}

        avg_precision = sum(f.precision * weights[f.name] for f in flavours_list)

        diagnostics = PolicyDiagnostics({})
        return PolicyResult(weights, avg_precision, diagnostics)
