"""Scheduling strategies for the carbon-aware scheduler.

This module provides a pluggable architecture for scheduling strategies.
Each strategy is implemented in a separate file for easy extension.

Available strategies:
- CreditGreedyPolicy: Spend credit on greener flavours while keeping error in check
- ForecastAwarePolicy: Adjust allowance based on carbon intensity trends
- ForecastAwareGlobalPolicy: Advanced strategy with global optimization using all signals
- P100Policy: Always push 100% to the highest precision flavour
- RoundRobinPolicy: Split traffic evenly between all flavours
- RandomPolicy: Use random weights at every push

Note: Throttling behavior is now configured via TrafficSchedule CR parameters
(throttleMin, throttleIntensityFloor, throttleIntensityCeiling), not via separate policies.

To add a new strategy:
1. Create a new file in this directory (e.g., my_strategy.py)
2. Implement a class that inherits from SchedulerPolicy
3. Import and register it in this __init__.py file
4. Add it to STRATEGY_REGISTRY in engine.py
"""

from .base import SchedulerPolicy
from .credit_greedy import CreditGreedyPolicy
from .forecast_aware import ForecastAwarePolicy
from .forecast_aware_global import ForecastAwareGlobalPolicy
from .p100 import P100Policy
from .random import RandomPolicy
from .round_robin import RoundRobinPolicy

__all__ = [
    "SchedulerPolicy",
    "CreditGreedyPolicy",
    "ForecastAwarePolicy",
    "ForecastAwareGlobalPolicy",
    "P100Policy",
    "RandomPolicy",
    "RoundRobinPolicy",
]
