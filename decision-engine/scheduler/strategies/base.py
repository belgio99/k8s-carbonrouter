"""Base class for scheduling strategies.

Note on terminology:
- Policy/Strategy: Scheduling algorithms like credit-greedy, forecast-aware
- Flavour: Precision variants like precision-30, precision-50, precision-100
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..ledger import CreditLedger
from ..models import FlavourProfile, ForecastSnapshot, PolicyResult


class SchedulerPolicy(ABC):
    """Abstract scheduling policy interface."""

    name: str

    def __init__(self, ledger: CreditLedger) -> None:
        self.ledger = ledger

    @abstractmethod
    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """Return a flavour distribution for the next scheduling window."""
