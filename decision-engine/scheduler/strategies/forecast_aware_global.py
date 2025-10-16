"""Forecast-Aware-Global scheduling strategy.

Advanced strategy that considers:
- Credit/debt in terms of average error
- Current slot greenness (carbon intensity)
- Forecast emissions for upcoming slots (extended look-ahead)
- Cumulative emissions so far
- Request demand forecasts for upcoming slots

This is the most comprehensive strategy, combining all available signals
to make globally-optimal scheduling decisions.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .credit_greedy import CreditGreedyPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyDiagnostics, PolicyResult

_LOGGER = logging.getLogger("scheduler.strategy.forecast-aware-global")


class ForecastAwareGlobalPolicy(CreditGreedyPolicy):
    """
    Advanced forecast-aware policy with global optimization.
    
    This strategy extends forecast-aware by:
    1. Tracking cumulative carbon emissions
    2. Using demand forecasts to anticipate load changes
    3. Extended look-ahead using the full forecast schedule
    4. Multi-factor scoring combining carbon, error, and demand
    """

    name = "forecast-aware-global"

    def __init__(self, *args, **kwargs):
        """Initialize with cumulative emissions tracker."""
        super().__init__(*args, **kwargs)
        self._cumulative_carbon: float = 0.0
        self._request_count: int = 0
        
    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """
        Evaluate scheduling decision using global optimization.
        
        Algorithm:
        1. Get base credit-greedy allocation
        2. Analyze carbon intensity trend (short-term and extended)
        3. Incorporate demand forecast to anticipate load spikes
        4. Adjust allowance based on cumulative emissions budget
        5. Apply multi-factor adjustments
        """
        flavours_list = [f for f in flavours if f.enabled]
        if not flavours_list:
            raise ValueError("no flavours enabled")

        # Get base allocation from credit-greedy
        base = super().evaluate(flavours_list[:], forecast)
        
        # If no forecast available, fall back to base strategy
        if not forecast:
            return base

        # =====================================================================
        # Factor 1: Carbon Intensity Trend Analysis
        # =====================================================================
        carbon_adjustment = self._compute_carbon_trend_adjustment(forecast)
        
        # =====================================================================
        # Factor 2: Demand Forecast Analysis
        # =====================================================================
        demand_adjustment = self._compute_demand_adjustment(forecast)
        
        # =====================================================================
        # Factor 3: Cumulative Emissions Budget
        # =====================================================================
        emissions_adjustment = self._compute_emissions_budget_adjustment(forecast)
        
        # =====================================================================
        # Factor 4: Extended Look-Ahead
        # =====================================================================
        lookahead_adjustment = self._compute_extended_lookahead_adjustment(forecast)
        
        # =====================================================================
        # Combine all adjustments
        # =====================================================================
        total_adjustment = (
            0.35 * carbon_adjustment +      # 35% weight on carbon trend
            0.25 * demand_adjustment +      # 25% weight on demand forecast
            0.25 * emissions_adjustment +   # 25% weight on emissions budget
            0.15 * lookahead_adjustment     # 15% weight on extended forecast
        )
        
        # Clamp total adjustment to reasonable bounds
        total_adjustment = max(-0.5, min(0.5, total_adjustment))
        
        # Apply adjustment to weights
        weights = self._apply_adjustment(base.weights, total_adjustment, flavours_list)
        
        # Calculate new average precision
        avg_precision = sum(
            weights[name] * self._precision_of_name(flavours_list, name)
            for name in weights
        )
        
        # Build comprehensive diagnostics
        diagnostics = PolicyDiagnostics({
            **base.diagnostics.fields,
            "carbon_adjustment": carbon_adjustment,
            "demand_adjustment": demand_adjustment,
            "emissions_adjustment": emissions_adjustment,
            "lookahead_adjustment": lookahead_adjustment,
            "total_adjustment": total_adjustment,
            "cumulative_carbon_gco2": self._cumulative_carbon,
            "request_count": float(self._request_count),
            "avg_carbon_per_request": (
                self._cumulative_carbon / self._request_count 
                if self._request_count > 0 else 0.0
            ),
        })
        
        _LOGGER.debug(
            "ForecastAwareGlobal: adj=%.3f (carbon=%.3f, demand=%.3f, emissions=%.3f, lookahead=%.3f)",
            total_adjustment, carbon_adjustment, demand_adjustment, 
            emissions_adjustment, lookahead_adjustment
        )
        
        return PolicyResult(weights, avg_precision, diagnostics)

    def _compute_carbon_trend_adjustment(
        self, 
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on carbon intensity trend.
        
        Returns:
            Adjustment factor in range [-1.0, +1.0]
            Negative = reduce green flavours (conserve credit)
            Positive = increase green flavours (spend credit)
        """
        if forecast.intensity_now is None or forecast.intensity_next is None:
            return 0.0
            
        current = forecast.intensity_now
        next_period = forecast.intensity_next
        
        if current <= 0:
            return 0.0
            
        # Calculate relative trend
        trend = (next_period - current) / current
        
        # Strong positive trend (intensity rising) → conserve credit
        # Strong negative trend (intensity falling) → spend credit
        if trend > 0.2:  # Rising by >20%
            return -0.8  # Strongly conserve
        elif trend > 0.05:  # Rising by >5%
            return -0.4  # Moderately conserve
        elif trend < -0.2:  # Falling by >20%
            return 0.8  # Strongly spend
        elif trend < -0.05:  # Falling by >5%
            return 0.4  # Moderately spend
        else:
            return trend * 2.0  # Linear scaling for small changes

    def _compute_demand_adjustment(
        self, 
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on demand forecast.
        
        If demand is expected to spike, we should conserve credit now
        to handle the spike with higher precision later.
        
        Returns:
            Adjustment factor in range [-1.0, +1.0]
        """
        if forecast.demand_now is None or forecast.demand_next is None:
            return 0.0
        
        current_demand = forecast.demand_now
        next_demand = forecast.demand_next
        
        if current_demand <= 0:
            return 0.0
        
        # Calculate relative demand change
        demand_ratio = next_demand / current_demand
        
        if demand_ratio > 1.5:  # Demand spike expected (>50% increase)
            return -0.6  # Strongly conserve credit for spike
        elif demand_ratio > 1.2:  # Moderate increase expected
            return -0.3  # Moderately conserve
        elif demand_ratio < 0.7:  # Demand drop expected (>30% decrease)
            return 0.4  # Can afford to spend credit
        elif demand_ratio < 0.85:  # Slight decrease
            return 0.2  # Slightly spend
        else:
            return 0.0  # Stable demand

    def _compute_emissions_budget_adjustment(
        self, 
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on cumulative emissions budget.
        
        If we've been emitting too much carbon, encourage greener flavours.
        If we've been very clean, we have more budget to spend.
        
        Returns:
            Adjustment factor in range [-1.0, +1.0]
        """
        if self._request_count < 10:  # Not enough data
            return 0.0
        
        if forecast.intensity_now is None or forecast.intensity_now <= 0:
            return 0.0
        
        # Calculate average carbon per request
        avg_carbon_per_req = self._cumulative_carbon / self._request_count
        
        # Compare with current intensity (as a proxy for "expected" emissions)
        # This is a heuristic: if we've been emitting more than current intensity,
        # we should be more conservative
        current_intensity = forecast.intensity_now
        
        if avg_carbon_per_req > current_intensity * 1.2:
            # We've been emitting significantly more than current rate
            return 0.5  # Push towards greener flavours
        elif avg_carbon_per_req > current_intensity * 1.05:
            return 0.2  # Slightly prefer greener
        elif avg_carbon_per_req < current_intensity * 0.8:
            # We've been very clean, can afford higher precision
            return -0.3
        else:
            return 0.0  # On track

    def _compute_extended_lookahead_adjustment(
        self, 
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on extended forecast schedule.
        
        Analyzes the full forecast schedule to identify upcoming
        very clean or very dirty periods.
        
        Returns:
            Adjustment factor in range [-1.0, +1.0]
        """
        if not forecast.schedule or forecast.intensity_now is None:
            return 0.0
        
        current = forecast.intensity_now
        if current <= 0:
            return 0.0
        
        # Analyze next 3-6 forecast points (typically 1.5-3 hours ahead)
        lookahead_points = forecast.schedule[:6]
        if len(lookahead_points) < 2:
            return 0.0
        
        # Calculate average intensity in lookahead window
        valid_forecasts = [
            p.forecast for p in lookahead_points 
            if p.forecast is not None and p.forecast > 0
        ]
        
        if not valid_forecasts:
            return 0.0
        
        avg_future = sum(valid_forecasts) / len(valid_forecasts)
        
        # Find min and max in lookahead
        min_future = min(valid_forecasts)
        max_future = max(valid_forecasts)
        
        # Calculate overall trend
        future_ratio = avg_future / current
        
        # Check for upcoming very clean period
        if min_future < current * 0.6:  # Very clean period ahead
            return -0.5  # Conserve credit for that clean period
        
        # Check for upcoming very dirty period  
        if max_future > current * 1.4:  # Very dirty period ahead
            return 0.6  # Spend credit now while it's cleaner
        
        # General trend
        if future_ratio > 1.3:
            return 0.4  # Future is much dirtier, spend now
        elif future_ratio > 1.1:
            return 0.2
        elif future_ratio < 0.8:
            return -0.3  # Future is much cleaner, conserve
        elif future_ratio < 0.9:
            return -0.15
        else:
            return 0.0

    def _apply_adjustment(
        self,
        base_weights: Dict[str, float],
        adjustment: float,
        flavours: List[FlavourProfile]
    ) -> Dict[str, float]:
        """
        Apply adjustment to base weights.
        
        Positive adjustment → shift towards greener (lower precision) flavours
        Negative adjustment → shift towards baseline (higher precision) flavours
        
        Args:
            base_weights: Original weights from base strategy
            adjustment: Adjustment factor in [-1.0, +1.0]
            flavours: List of available flavours
            
        Returns:
            Adjusted weights dictionary
        """
        if abs(adjustment) < 0.01:  # No significant adjustment
            return base_weights
        
        # Sort flavours by precision (descending)
        sorted_flavours = sorted(flavours, key=lambda f: f.precision, reverse=True)
        
        if not sorted_flavours:
            return base_weights
        
        baseline_name = sorted_flavours[0].name
        
        # Create new weights
        weights = dict(base_weights)
        
        if adjustment > 0:  # Shift towards greener flavours
            # Reduce baseline weight, increase others proportionally
            baseline_weight = weights.get(baseline_name, 0.0)
            reduction = min(baseline_weight * adjustment * 0.8, baseline_weight - 0.1)
            
            if reduction > 0:
                weights[baseline_name] = max(0.1, baseline_weight - reduction)
                
                # Distribute reduction to greener flavours
                other_flavours = [f for f in sorted_flavours if f.name != baseline_name]
                if other_flavours:
                    # Weight by carbon score
                    scores = [
                        self._carbon_score(sorted_flavours[0], f) 
                        for f in other_flavours
                    ]
                    score_sum = sum(scores) or len(scores)
                    
                    for f, score in zip(other_flavours, scores):
                        weights[f.name] = weights.get(f.name, 0.0) + (
                            reduction * (score / score_sum)
                        )
        
        else:  # adjustment < 0, shift towards baseline
            # Increase baseline weight, reduce others proportionally
            increase = abs(adjustment) * 0.5
            
            # Take weight from non-baseline flavours
            other_total = sum(
                w for name, w in weights.items() if name != baseline_name
            )
            
            if other_total > 0.2:  # Enough weight to take from
                reduction_factor = 1.0 - (increase / other_total)
                reduction_factor = max(0.5, reduction_factor)
                
                reclaimed = 0.0
                for name in weights:
                    if name != baseline_name:
                        old_weight = weights[name]
                        new_weight = old_weight * reduction_factor
                        weights[name] = max(0.05, new_weight)
                        reclaimed += old_weight - weights[name]
                
                weights[baseline_name] = weights.get(baseline_name, 0.0) + reclaimed
        
        # Normalize weights
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        
        return weights

    def update_cumulative_emissions(
        self,
        flavour_name: str,
        flavours: List[FlavourProfile]
    ) -> None:
        """
        Update cumulative emissions tracker after processing a request.
        
        This should be called by the engine after each request is processed.
        
        Args:
            flavour_name: Name of the flavour that processed the request
            flavours: List of available flavours
        """
        for flavour in flavours:
            if flavour.name == flavour_name:
                self._cumulative_carbon += flavour.carbon_intensity
                self._request_count += 1
                break
    
    def reset_cumulative_emissions(self) -> None:
        """Reset cumulative emissions tracker (e.g., at start of new period)."""
        self._cumulative_carbon = 0.0
        self._request_count = 0
