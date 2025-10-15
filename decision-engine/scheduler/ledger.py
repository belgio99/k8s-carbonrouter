"""
Credit Ledger for Quality-of-Service Tracking

The credit ledger maintains a balance between target quality (error threshold)
and realized quality (actual error from completed requests). This enables
the scheduler to:
- Accumulate "credit" when using high-precision strategies (quality surplus)
- Spend "credit" when using low-precision strategies (quality deficit)
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
    - Balance: Cumulative difference between target and realized error
    - Velocity: Average rate of credit change (trend indicator)
    
    Positive balance = Quality surplus (can use cheaper strategies)
    Negative balance = Quality deficit (need higher precision strategies)
    
    Attributes:
        target_error: Target quality error threshold (e.g., 0.05 = 5% error)
        credit_min: Minimum allowed credit balance (quality debt limit)
        credit_max: Maximum allowed credit balance (quality surplus cap)
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
        2. Credit delta (target_error - realized_error)
        3. Updates balance, clamped to [credit_min, credit_max]
        
        Args:
            realised_precision: Actual precision of completed request (0.0-1.0)
            
        Returns:
            Updated credit balance
            
        Example:
            If target_error=0.05 and realized_precision=0.97:
            - realized_error = 1 - 0.97 = 0.03
            - delta = 0.05 - 0.03 = +0.02 (credit surplus)
        """
        realised_error = max(0.0, 1.0 - realised_precision)
        delta = self.target_error - realised_error
        self._history.append(delta)
        self._balance = max(self.credit_min, min(self.credit_max, self._balance + delta))
        return self._balance

    def velocity(self) -> float:
        """
        Calculate average credit change rate over the sliding window.
        
        Positive velocity = Quality improving (accumulating credits)
        Negative velocity = Quality degrading (losing credits)
        Zero velocity = Stable quality at target level
        
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
