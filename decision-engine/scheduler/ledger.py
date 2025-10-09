"""Credit ledger implementation for the scheduler."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass
class CreditLedger:
    """Tracks credit balance between realised and target error."""

    target_error: float
    credit_min: float
    credit_max: float
    window_size: int

    def __post_init__(self) -> None:
        self._history: Deque[float] = deque(maxlen=self.window_size)
        self._balance: float = 0.0

    @property
    def balance(self) -> float:
        return self._balance

    def update(self, realised_precision: float) -> float:
        """Update the ledger given the precision of a completed request."""

        realised_error = max(0.0, 1.0 - realised_precision)
        delta = self.target_error - realised_error
        self._history.append(delta)
        self._balance = max(self.credit_min, min(self.credit_max, self._balance + delta))
        return self._balance

    def velocity(self) -> float:
        """Return the average credit delta over the sliding window."""

        if not self._history:
            return 0.0
        return sum(self._history) / len(self._history)

    def reset(self) -> None:
        self._history.clear()
        self._balance = 0.0
