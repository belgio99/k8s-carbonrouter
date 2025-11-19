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
        self._evaluation_count: int = 0  # Count of evaluations, not individual requests
        self.demand_pattern: Optional[List[int]] = None
        try:
            import json
            # Load from container root (copied by Dockerfile)
            with open("demand_scenario.json", encoding="utf-8") as f:
                scenario = json.load(f)
                self.demand_pattern = scenario["pattern"]
                _LOGGER.info("Loaded demand pattern with %d points from demand_scenario.json", len(self.demand_pattern))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            _LOGGER.warning("demand_scenario.json not found or invalid: %s. Demand forecasting will not work correctly.", e)

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
        # Pre-computation: Demand Forecast Override
        # =====================================================================
        if self.demand_pattern:
            num_points = len(self.demand_pattern)
            if num_points > 0:
                current_index = self._evaluation_count % num_points
                next_index = (self._evaluation_count + 1) % num_points
                forecast.demand_now = float(self.demand_pattern[current_index])
                forecast.demand_next = float(self.demand_pattern[next_index])

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
        # Combine all adjustments with BALANCED weighting
        # =====================================================================
        credit_pressure = self._credit_pressure_adjustment()

        # BALANCED WEIGHTS: Reduce lookahead dominance, boost workload/constraints
        # Distribution:
        #   - Forward-looking (carbon signals): 50% (carbon 15% + lookahead 35%)
        #   - Workload awareness (demand): 20%
        #   - Backward-looking (constraints): 30% (emissions 10% + credit 20%)
        total_adjustment = (
            0.15 * carbon_adjustment +      # 15% - immediate carbon trend response
            0.20 * demand_adjustment +      # 20% - workload awareness (2x boost)
            0.10 * emissions_adjustment +   # 10% - carbon budget accountability
            0.35 * lookahead_adjustment +   # 35% - strategic carbon forecast (reduced from 55%)
            0.20 * credit_pressure          # 20% - quality constraint (2x boost)
        )

        # WIDER clamp range to allow stronger signals through
        # ±2.0 allows for much more aggressive carbon-aware routing decisions
        total_adjustment = max(-2.0, min(2.0, total_adjustment))
        
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
            "credit_pressure": credit_pressure,
            "total_adjustment": total_adjustment,
            "cumulative_carbon_gco2": self._cumulative_carbon,
            "evaluation_count": float(self._evaluation_count),
            "avg_carbon_per_evaluation": (
                self._cumulative_carbon / self._evaluation_count
                if self._evaluation_count > 0 else 0.0
            ),
            "demand_now": forecast.demand_now if forecast.demand_now is not None else 0.0,
            "demand_next": forecast.demand_next if forecast.demand_next is not None else 0.0,
        })
        
        _LOGGER.debug(
            "ForecastAwareGlobal: adj=%.3f (carbon=%.3f, demand=%.3f, emissions=%.3f, lookahead=%.3f) | demand_now=%.1f demand_next=%.1f",
            total_adjustment, carbon_adjustment, demand_adjustment,
            emissions_adjustment, lookahead_adjustment,
            forecast.demand_now if forecast.demand_now is not None else 0.0,
            forecast.demand_next if forecast.demand_next is not None else 0.0
        )

        # Update cumulative emissions tracking based on commanded weights
        # Calculate weighted average carbon intensity from this evaluation
        weighted_carbon = sum(
            weights.get(f.name, 0.0) * (f.carbon_intensity or 0.0)
            for f in flavours_list
        )
        self._cumulative_carbon += weighted_carbon
        self._evaluation_count += 1

        return PolicyResult(weights, avg_precision, diagnostics)

    def _compute_carbon_trend_adjustment(
        self,
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on carbon intensity trend.

        Returns:
            Adjustment factor (NO PRE-CLAMPING - can exceed ±1.0)
            Positive = reduce p100 (conserve quality, use greener flavours)
            Negative = increase p100 (spend quality, use baseline)
        """
        if forecast.intensity_now is None or forecast.intensity_next is None:
            return 0.0

        current = forecast.intensity_now
        next_period = forecast.intensity_next

        if current <= 0:
            return 0.0

        # Calculate relative trend
        trend = (next_period - current) / current

        # MODERATE response for gentle nudges
        # 50% carbon increase → ~-0.8 adjustment (before weighting)
        # 50% carbon decrease → ~+0.8 adjustment (before weighting)
        import math
        scaled_trend = trend * 4.0  # Reduced from 8.0 for gentler response
        adjustment = -0.8 * math.tanh(scaled_trend)  # Reduced from -1.5 to -0.8

        # Interpretation:
        # - Positive trend (rising carbon) → negative adjustment → increase p100 (use quality now)
        # - Negative trend (falling carbon) → positive adjustment → decrease p100 (save for later)

        return adjustment  # No clamping - let weighting handle it

    def _compute_demand_adjustment(
        self,
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute CARBON-AWARE adjustment based on demand forecast.

        Key insight: Rising demand should be handled differently based on
        current carbon intensity:
        - Low carbon + rising demand = OPPORTUNISTIC (serve more NOW)
        - High carbon + rising demand = DEFENSIVE (reduce quality NOW)

        Returns:
            Adjustment factor (NO PRE-CLAMPING - can exceed ±1.0)
        """
        if forecast.demand_now is None or forecast.demand_next is None:
            return 0.0

        current_demand = forecast.demand_now
        next_demand = forecast.demand_next

        if current_demand <= 0:
            return 0.0

        # Calculate relative demand change
        demand_ratio = next_demand / current_demand

        import math
        demand_change = demand_ratio - 1.0  # -0.5 to +1.0 typical range
        scaled_change = demand_change * 5.0  # Scale up for stronger signal
        base_adjustment = math.tanh(scaled_change)  # -1.0 to +1.0

        # Normalize carbon intensity (assume 40-300 gCO2/kWh range)
        carbon_min = 40.0
        carbon_max = 300.0
        carbon_normalized = (forecast.intensity_now - carbon_min) / (carbon_max - carbon_min)
        carbon_normalized = max(0.0, min(1.0, carbon_normalized))  # Clamp to 0-1

        # CARBON-AWARE LOGIC:
        if demand_ratio > 1.0:  # Demand is RISING
            if carbon_normalized < 0.5:  # LOW carbon NOW (< 170 gCO2/kWh)
                # OPPORTUNISTIC: Serve MORE now while carbon is cheap
                # This exploits low-carbon windows before demand peaks
                adjustment = +2.0 * base_adjustment  # Inverted sign: rising demand → +adjustment
            else:  # HIGH carbon NOW (≥ 170 gCO2/kWh)
                # DEFENSIVE: Reduce quality NOW, save credit for later
                adjustment = -2.0 * base_adjustment  # Original sign: rising demand → -adjustment
        else:  # Demand is FALLING or STABLE
            # Always take advantage of demand drop to increase quality
            # (negative base_adjustment becomes positive after negation)
            adjustment = -2.0 * base_adjustment

        return adjustment  # No clamping

    def _compute_emissions_budget_adjustment(
        self,
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on cumulative emissions budget.

        If we've been emitting too much carbon, encourage greener flavours.
        If we've been very clean, we have more budget to spend.

        Returns:
            Adjustment factor (NO PRE-CLAMPING - can exceed ±1.0)
        """
        if self._evaluation_count < 10:  # Not enough data
            return 0.0

        if forecast.intensity_now is None or forecast.intensity_now <= 0:
            return 0.0

        # Calculate average carbon per evaluation (weighted intensity commanded per evaluation cycle)
        avg_carbon_per_eval = self._cumulative_carbon / self._evaluation_count

        # Compare with current intensity (as a proxy for "expected" emissions)
        current_intensity = forecast.intensity_now

        # CONTINUOUS function instead of hard thresholds
        # avg = 1.5× current → +1.5 adjustment (push greener)
        # avg = 1.0× current → 0.0 adjustment (on track)
        # avg = 0.7× current → -1.0 adjustment (can spend more)
        ratio = avg_carbon_per_eval / current_intensity
        import math
        # Scale deviation from 1.0 for stronger signal
        deviation = (ratio - 1.0) * 5.0  # Amplify deviations
        adjustment = 1.5 * math.tanh(deviation)  # Amplified response

        return adjustment  # No clamping

    def _compute_extended_lookahead_adjustment(
        self,
        forecast: ForecastSnapshot
    ) -> float:
        """
        Compute adjustment based on extended forecast schedule.

        Analyzes the full forecast schedule to identify upcoming
        very clean or very dirty periods.

        Returns:
            Adjustment factor (NO PRE-CLAMPING - can exceed ±1.0)
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

        # Calculate trend using weighted average (emphasize near-term more)
        weighted_sum = 0.0
        weight_total = 0.0
        for i, forecast_val in enumerate(valid_forecasts):
            weight = 1.0 / (1.0 + i * 0.3)  # Exponential decay
            weighted_sum += forecast_val * weight
            weight_total += weight

        weighted_avg = weighted_sum / weight_total if weight_total > 0 else avg_future
        future_ratio = weighted_avg / current

        # AMPLIFIED response to future trend
        # If future is cleaner → conserve quality (positive adjustment → decrease p100)
        # If future is dirtier → spend quality now (negative adjustment → increase p100)
        import math

        # Scale ratio to log space for better sensitivity around 1.0
        trend_factor = math.log(future_ratio)

        # GENTLE tanh response for subtle nudges (not dramatic swings)
        # Reduced multipliers: allows 5-10% weight adjustments instead of 40-50%
        adjustment = -0.5 * math.tanh(trend_factor * 1.5)

        # Additional factors: check for extreme min/max
        min_ratio = min_future / current
        max_ratio = max_future / current

        # Gentle extreme opportunity/threat signals
        if min_ratio < 0.7:  # Very clean period ahead
            adjustment += -0.3  # Extra conserve (gentle nudge)
        if max_ratio > 1.3:  # Very dirty period ahead
            adjustment += 0.3   # Extra spend now (gentle nudge)

        return adjustment  # No clamping - let weighting handle it

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
            adjustment: Adjustment factor in [-2.0, +2.0] (WIDENED for stronger signals)
            flavours: List of available flavours

        Returns:
            Adjusted weights dictionary
        """
        if abs(adjustment) < 0.02:  # No significant adjustment (threshold scaled for ±2.0 range)
            return base_weights

        # Sort flavours by precision (descending)
        sorted_flavours = sorted(flavours, key=lambda f: f.precision, reverse=True)

        if not sorted_flavours:
            return base_weights

        baseline_name = sorted_flavours[0].name

        # Create new weights
        weights = dict(base_weights)

        if adjustment > 0:  # Shift towards greener flavours
            baseline_weight = weights.get(baseline_name, 0.0)
            baseline_floor = 0.01  # Reduced from 0.02 for more aggressive shifts

            # Scale reduction by adjustment magnitude (now in ±2.0 range)
            # adjustment=2.0 → near-complete removal of baseline
            # adjustment=0.5 → moderate shift
            reduction_factor = min(1.0, adjustment / 2.0)  # Normalize to [0, 1]
            reduction = min(baseline_weight * (0.5 + reduction_factor * 0.48), baseline_weight - baseline_floor)

            if reduction > 0:
                weights[baseline_name] = max(baseline_floor, baseline_weight - reduction)
                other_flavours = [f for f in sorted_flavours if f.name != baseline_name]
                if other_flavours:
                    scores = [
                        self._carbon_score(sorted_flavours[0], f)
                        for f in other_flavours
                    ]
                    score_sum = sum(scores) or len(scores)
                    for f, score in zip(other_flavours, scores):
                        weights[f.name] = weights.get(f.name, 0.0) + reduction * (score / score_sum)

        else:  # adjustment < 0, shift towards baseline
            other_total = sum(
                w for name, w in weights.items() if name != baseline_name
            )
            if other_total > 0:
                # Scale reduction by adjustment magnitude (now in ±2.0 range)
                # adjustment=-2.0 → near-complete shift to baseline
                # adjustment=-0.5 → moderate shift
                reduction_intensity = min(1.0, abs(adjustment) / 2.0)  # Normalize to [0, 1]
                reduction_factor = max(0.1, 1.0 - reduction_intensity * 0.9)  # More aggressive floor

                reclaimed = 0.0
                for name in list(weights.keys()):
                    if name == baseline_name:
                        continue
                    old_weight = weights[name]
                    new_weight = max(0.005, old_weight * reduction_factor)  # Lower floor for stronger shifts
                    reclaimed += old_weight - new_weight
                    weights[name] = new_weight
                weights[baseline_name] = min(0.99, weights.get(baseline_name, 0.0) + reclaimed)

        # Normalize weights
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def _credit_pressure_adjustment(self) -> float:
        """
        Compute adjustment based on credit ledger balance.

        Acts as quality guard-rail to prevent extreme debt/surplus.

        Returns:
            Adjustment factor (NO PRE-CLAMPING - can exceed ±1.0)
        """
        span = (self.ledger.credit_max - self.ledger.credit_min) or 1.0
        normalised = (self.ledger.balance - self.ledger.credit_min) / span
        # steer towards the middle of the band (≈0.5)
        # Positive balance = surplus, Negative balance = debt
        target = 0.5
        deviation = normalised - target
        # AMPLIFIED response: Allow stronger guard-rail signals
        # Deep debt (balance near -1.0) → large negative adjustment → spend quality to recover
        # Large surplus (balance near +1.0) → large positive adjustment → use greener flavours
        return deviation * 1.5  # Increased from 0.6 to 1.5, no clamping

    def update_cumulative_emissions(
        self,
        flavour_name: str,
        flavours: List[FlavourProfile]
    ) -> None:
        """
        Update cumulative emissions tracker after processing a request.

        This should be called by the engine after each request is processed.
        NOTE: This method is currently not used; emissions are tracked per evaluation instead.

        Args:
            flavour_name: Name of the flavour that processed the request
            flavours: List of available flavours
        """
        for flavour in flavours:
            if flavour.name == flavour_name:
                self._cumulative_carbon += flavour.carbon_intensity
                self._evaluation_count += 1
                break

    def reset_cumulative_emissions(self) -> None:
        """Reset cumulative emissions tracker (e.g., at start of new period)."""
        self._cumulative_carbon = 0.0
        self._evaluation_count = 0
