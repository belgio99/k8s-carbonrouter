"""
Credit Ledger for Quality-of-Service Tracking

The credit ledger maintains a balance between target quality (error threshold)
and realized quality (actual error from completed requests). This enables
the scheduler to:
- Spend "credit" when using high-precision strategies (quality surplus, low error)
- Accumulate "credit" when using low-precision strategies (quality deficit, high error)
- Balance quality over time rather than meeting thresholds for every request

The ledger uses a sliding window to track recent quality trends (velocity)
and clamps the balance to prevent unbounded credit accumulation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass
class CreditLedger:
    """
    Tracks quality credit balance over time.

    The ledger computes:
    - Balance: Cumulative difference between realized and target error (range: -1.0 to +1.0)
    - Velocity: Average rate of credit change (trend indicator)

    Positive balance = Quality surplus accumulated (earned credit from high-precision use)
    Negative balance = Quality deficit consumed (spent credit on low-precision use)
    Zero balance = Neutral state (quality at target level)

    Attributes:
        target_error: Target quality error threshold (e.g., 0.1 = 10% error)
        credit_min: Minimum allowed credit balance (quality debt limit, typically -1.0)
        credit_max: Maximum allowed credit balance (quality surplus cap, typically +1.0)
        window_size: Number of recent requests for velocity calculation
    """

    target_error: float
    credit_min: float
    credit_max: float
    window_size: int

    def __post_init__(self) -> None:
        """Initialize ledger state with empty history."""
        self._history: Deque[float] = deque(maxlen=self.window_size)
        self._balance: float = 0.0

    @property
    def balance(self) -> float:
        """Get current credit balance."""
        return self._balance

    def update(self, realised_precision: float) -> float:
        """
        Update ledger based on the precision of a completed request.

        Calculates:
        1. Realized error from precision (error = 1 - precision)
        2. Credit delta (realized_error - target_error)
        3. Updates balance, clamped to [credit_min, credit_max] (typically -1.0 to +1.0)

        Args:
            realised_precision: Actual precision of completed request (0.0-1.0)

        Returns:
            Updated credit balance

        Example:
            If target_error=0.1 and realized_precision=0.95 (high-precision):
            - realized_error = 1 - 0.95 = 0.05
            - delta = 0.1 - 0.05 = +0.05 (earning credit)
            After 20 such requests: balance = +0.05 × 20 = +1.0 (max surplus)

            If target_error=0.1 and realized_precision=0.3 (low-precision):
            - realized_error = 1 - 0.3 = 0.7
            - delta = 0.1 - 0.7 = -0.6 (spending credit / accumulating debt)
        """
        realised_error = max(0.0, 1.0 - realised_precision)
        delta = self.target_error - realised_error  # FIXED: Flipped sign for intuitive balance
        self._history.append(delta)
        self._balance = max(self.credit_min, min(self.credit_max, self._balance + delta))
        return self._balance

    def velocity(self) -> float:
        """
        Calculate average credit change rate over the sliding window.

        Positive velocity = Using high-precision strategies (earning credits)
        Negative velocity = Using low-precision strategies (spending credits)
        Zero velocity = Balanced quality at target level

        Returns:
            Average credit delta per request over window
        """
        if not self._history:
            return 0.0
        return sum(self._history) / len(self._history)

    def reset(self) -> None:
        """Reset ledger to initial state (zero balance, empty history)."""
        self._history.clear()
        self._balance = 0.0
