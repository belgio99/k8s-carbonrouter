"""Forecast-Aware-Global (No Throttle) scheduling strategy.

This is identical to forecast-aware-global but configured to disable autoscaling throttling.
Used as a baseline for comparing the carbon savings from throttling-based temporal shifting.

The only difference is the strategy name - the actual throttling is disabled via
configuration (throttle_min=1.0) in the benchmark script.
"""

from __future__ import annotations

from .forecast_aware_global import ForecastAwareGlobalPolicy


class ForecastAwareGlobalNoThrottlePolicy(ForecastAwareGlobalPolicy):
    """
    Forecast-aware-global policy without autoscaling throttling.

    This strategy is identical to ForecastAwareGlobalPolicy but is registered
    under a different name to allow benchmark scripts to apply different
    throttling configurations.

    When used with throttle_min=1.0 config, the system will:
    - Still optimize precision/carbon tradeoffs via flavour selection
    - NOT limit replica counts based on carbon intensity or credit balance
    - Scale freely to meet demand without carbon-aware throttling

    This provides a baseline to measure the carbon savings achieved by
    throttling + temporal shifting (queuing) in the standard forecast-aware-global.
    """

    name = "forecast-aware-global-no-throttle"
