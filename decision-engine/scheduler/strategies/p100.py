"""P100 scheduling strategy.

Always pushes 100% weight to the highest precision flavour.
This is a baseline strategy that doesn't consider carbon intensity.
"""

from __future__ import annotations

from typing import Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult


class P100Policy(SchedulerPolicy):
    """Always push 100% to the highest precision flavour."""

    name = "p100"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        best = max(flavours_list, key=lambda f: f.precision)
        weights = {best.name: 1.0}

        diagnostics = PolicyDiagnostics({"selected_flavour": best.precision})
        return PolicyResult(weights, best.precision, diagnostics)
