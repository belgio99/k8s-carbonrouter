"""Round-Robin scheduling strategy.

Splits traffic evenly between all enabled flavours.
This is a baseline strategy that doesn't consider carbon intensity or precision.
"""

from __future__ import annotations

from typing import Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class RoundRobinPolicy(SchedulerPolicy):
    """Split traffic evenly between all flavours."""

    name = "round-robin"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        weight_per_flavour = 1.0 / len(flavours_list)
        weights = {f.name: weight_per_flavour for f in flavours_list}

        avg_precision = sum(f.precision for f in flavours_list) / len(flavours_list)

        diagnostics = PolicyDiagnostics({"num_flavours": float(len(flavours_list))})
        return PolicyResult(weights, avg_precision, diagnostics)
