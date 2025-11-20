# Scheduling Strategies

This directory contains the scheduling strategies (policies) for the carbon-aware scheduler.

## Architecture

Each strategy is implemented in a separate file for modularity and easy extension. All strategies inherit from the `SchedulerPolicy` abstract base class defined in `base.py`.

## Available Strategies

### 1. Credit-Greedy (`credit_greedy.py`)
- **Name**: `credit-greedy`
- **Description**: Spends credit on greener flavours while keeping error in check
- **Key Features**:
  - Allocates traffic based on available credit balance
  - Prioritizes flavours with better carbon scores
  - Balances carbon reduction with precision requirements

### 2. Forecast-Aware (`forecast_aware.py`)
- **Name**: `forecast-aware`
- **Description**: Extends Credit-Greedy by adjusting allowance based on carbon intensity trends
- **Key Features**:
  - Considers future carbon intensity forecasts
  - Adjusts traffic distribution proactively based on predicted trends
  - Reduces precision when carbon intensity is expected to increase

### 3. Forecast-Aware-Global (`forecast_aware_global.py`) ⭐ **ADVANCED**

- **Name**: `forecast-aware-global`
- **Description**: Most comprehensive strategy with global optimization using all available signals
- **Key Features**:
  - ✅ **Credit/Debt Tracking**: Uses credit ledger for quality-carbon balance
  - ✅ **Current Greenness**: Considers current carbon intensity
  - ✅ **Carbon Forecasts**: Short-term and extended look-ahead (up to 3 hours)
  - ✅ **Demand Forecasts**: Anticipates load spikes and conserves credit accordingly
  - ✅ **Cumulative Emissions**: Tracks total carbon emissions and adjusts to stay on budget
  - ✅ **Multi-Factor Scoring**: Combines all signals with weighted adjustments:
    - 35% weight on carbon intensity trend
    - 25% weight on demand forecast
    - 25% weight on emissions budget
    - 15% weight on extended forecast look-ahead
- **Use Cases**:
  - Production environments with high carbon reduction targets
  - Scenarios with variable workload patterns
  - When comprehensive carbon+quality optimization is critical
  - Research and comparison of different scheduling approaches

## Adding a New Strategy

To add a custom scheduling strategy:

### 1. Create a New File

Create a new Python file in this directory (e.g., `my_custom_strategy.py`):

```python
"""My Custom Strategy description."""

from __future__ import annotations

from typing import Optional

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyResult, PolicyDiagnostics


class MyCustomStrategy(SchedulerPolicy):
    """Brief description of your strategy."""

    name = "my-custom"  # Unique identifier for your strategy

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """
        Implement your scheduling logic here.
        
        Args:
            flavours: List of available precision flavours
            forecast: Optional carbon intensity forecast data
            
        Returns:
            PolicyResult with traffic weights and diagnostics
        """
        # Your implementation here
        # 1. Analyze flavours and forecast
        # 2. Compute traffic weights (dict mapping flavour name to weight 0-1)
        # 3. Calculate average precision
        # 4. Create diagnostics for monitoring
        
        weights = {}  # Your weight calculation
        avg_precision = 0.0  # Your precision calculation
        diagnostics = PolicyDiagnostics({
            "custom_metric": 0.0,
        })
        
        return PolicyResult(weights, avg_precision, diagnostics)
```

### 2. Register Your Strategy

Add your strategy to `__init__.py`:

```python
from .my_custom_strategy import MyCustomStrategy

__all__ = [
    # ... existing strategies ...
    "MyCustomStrategy",
]
```

### 3. Update Engine Configuration

Add your strategy to the policy builders in `engine.py`:

```python
_POLICY_BUILDERS: Dict[str, type[SchedulerPolicy]] = {
    # ... existing strategies ...
    "my-custom": MyCustomStrategy,
}
```

### 4. Use Your Strategy

Configure your TrafficSchedule to use the new strategy:

```yaml
apiVersion: carbonrouter.belgio99.io/v1alpha1
kind: TrafficSchedule
metadata:
  name: my-schedule
spec:
  policy: my-custom  # Use your strategy name here
  # ... rest of configuration ...
```

## Strategy Interface

All strategies must implement the `SchedulerPolicy` interface:

```python
class SchedulerPolicy(ABC):
    name: str  # Unique strategy identifier
    
    def __init__(self, ledger: CreditLedger) -> None:
        """Initialize with a credit ledger."""
        
    @abstractmethod
    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        """Return traffic distribution for the next scheduling window."""
```

## Key Components

- **FlavourProfile**: Represents a precision variant (e.g., precision-30, precision-50)
  - `name`: Flavour identifier
  - `precision`: Target precision level (0-1)
  - `carbon_intensity`: Current carbon intensity (gCO2/kWh)
  - `enabled`: Whether the flavour is currently available

- **ForecastSnapshot**: Carbon intensity forecast data
  - `intensity_now`: Current carbon intensity
  - `intensity_next`: Predicted next-window intensity
  - `timestamp`: When the forecast was generated

- **PolicyResult**: Output of strategy evaluation
  - `weights`: Dict mapping flavour names to traffic weights (0-1, sum to 1)
  - `avg_precision`: Expected average precision with this distribution
  - `diagnostics`: Custom metrics for monitoring

- **CreditLedger**: Quality credit tracking
  - `balance`: Current credit balance
  - `credit_max`: Maximum allowed credit

## Testing Your Strategy

Before deploying:

1. Test locally with various flavour configurations
2. Verify weights sum to 1.0
3. Check behavior with different credit balances
4. Test with and without forecast data
5. Monitor diagnostics in Prometheus/Grafana

## Best Practices

- **Weight Normalization**: Always ensure weights sum to 1.0
- **Edge Cases**: Handle scenarios with no enabled flavours, zero credit, etc.
- **Diagnostics**: Export relevant metrics for monitoring and debugging
- **Documentation**: Document the strategy's decision logic clearly
- **Testing**: Validate behavior across different conditions before deployment
