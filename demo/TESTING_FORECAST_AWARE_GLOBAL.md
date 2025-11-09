# Testing Forecast-Aware-Global Strategy

This guide explains how to test and validate the new `forecast-aware-global` scheduling strategy.

## Quick Start

### 1. Apply the Example Configuration

```bash
kubectl apply -f demo/forecast-aware-global-example.yaml
```

This creates:
- A `TrafficSchedule` configured with `forecast-aware-global` policy
- Three deployment variants (high/medium/low precision)

### 2. Monitor the Strategy

Check the schedule decisions:

```bash
# Get the current schedule
kubectl get trafficschedule example-forecast-aware-global -o yaml

# Watch the status in real-time
kubectl get trafficschedule example-forecast-aware-global -w
```

### 3. View Diagnostics

Query the decision engine directly:

```bash
# Port-forward to decision engine
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080

# Get schedule with diagnostics
curl http://localhost:8080/schedule/default/example-forecast-aware-global | jq .
```

Expected diagnostics:

```json
{
  "diagnostics": {
    "credit_balance": 0.36,
    "carbon_adjustment": -0.234,
    "demand_adjustment": -0.156,
    "emissions_adjustment": 0.089,
    "lookahead_adjustment": 0.045,
    "total_adjustment": -0.187,
    "cumulative_carbon_gco2": 1456.78,
    "request_count": 3421.0,
    "avg_carbon_per_request": 0.426
  }
}
```

## Prometheus Metrics

Query Grafana or Prometheus directly:

```promql
# Credit balance over time
scheduler_credit_balance{policy="forecast-aware-global"}

# Average precision trend
scheduler_avg_precision{policy="forecast-aware-global"}

# Credit velocity (rate of change)
scheduler_credit_velocity{policy="forecast-aware-global"}

# Carbon forecast with timestamps
scheduler_forecast_intensity_timestamped{policy="forecast-aware-global"}
```

## Comparing Strategies

Run the same workload with different strategies to compare:

### Test 1: forecast-aware-global vs credit-greedy

```bash
# Deploy with forecast-aware-global
kubectl apply -f demo/forecast-aware-global-example.yaml

# Monitor for 30 minutes
# Record: cumulative_carbon_gco2, avg_carbon_per_request, avg_precision

# Switch to credit-greedy
kubectl patch trafficschedule example-forecast-aware-global \
  --type merge -p '{"spec":{"config":{"policy":"credit-greedy"}}}'

# Monitor for another 30 minutes
# Compare results
```

### Test 2: Variable Load Patterns

```bash
# Generate load spike
kubectl run load-generator --image=busybox --restart=Never -- \
  sh -c "while true; do wget -q -O- http://my-ml-service; done"

# Observe how forecast-aware-global anticipates and prepares for the spike
# Check demand_adjustment in diagnostics
```

### Test 3: Carbon Intensity Changes

Monitor during times of day with varying carbon intensity:

- **Morning (7-9am)**: Increasing intensity → expect negative carbon_adjustment
- **Midday (12-2pm)**: Peak intensity → expect conservative strategy
- **Evening (6-8pm)**: Decreasing intensity → expect positive carbon_adjustment
- **Night (11pm-5am)**: Low intensity → expect aggressive green strategy

## Expected Behavior

### Scenario: Rising Carbon Intensity

```
Time: 08:00
intensity_now: 150 gCO2/kWh
intensity_next: 220 gCO2/kWh (↑47%)

Expected:
- carbon_adjustment: -0.8 (strongly conserve credit)
- total_adjustment: ≈ -0.28
- Result: Shift towards high-precision (baseline) flavours
```

### Scenario: Load Spike Anticipated

```
Time: 10:00
demand_now: 100 req/s
demand_next: 180 req/s (↑80%)

Expected:
- demand_adjustment: -0.6 (conserve for spike)
- Result: Use higher precision now to build credit
```

### Scenario: Over Budget

```
cumulative_carbon: 5400 gCO2
request_count: 3000
avg: 1.8 gCO2/req
current_intensity: 1.2 gCO2/kWh (50% over)

Expected:
- emissions_adjustment: +0.5 (push towards greener)
- Result: Prefer low-carbon flavours
```

## Validation Checklist

- [ ] Strategy loads without errors in decision-engine logs
- [ ] Diagnostics show all 6 adjustment factors
- [ ] cumulative_carbon_gco2 increases over time
- [ ] Adjustments respond to forecast changes
- [ ] Weights shift appropriately based on adjustments
- [ ] Credit balance stays within [-1.0, +1.0]
- [ ] Average precision respects targetError constraint

## Troubleshooting

### No Forecasts Available

```bash
# Check carbon API connectivity
kubectl logs -n carbonshift deploy/decision-engine | grep -i "carbon"

# Verify forecast endpoint
curl http://localhost:8080/forecast/default/example-forecast-aware-global
```

### Strategy Not Selected

```bash
# Check policy configuration
kubectl get trafficschedule example-forecast-aware-global -o jsonpath='{.spec.config.policy}'

# Should return: forecast-aware-global
```

### No Adjustments Applied

```bash
# Check if forecast data is valid
curl http://localhost:8080/schedule/default/example-forecast-aware-global | \
  jq '.forecast'

# Should show intensity_now, intensity_next, demand_now, demand_next
```

## Manual Carbon Intensity Override (for Testing)

Instead of using real Carbon Intensity UK API data, you can set manual carbon intensity values for controlled testing:

### Method 1: Manual Schedule Override via API

Set a complete manual schedule with custom carbon intensity:

```bash
# Port-forward to decision engine
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080

# Set manual schedule with custom carbon intensity
curl -X POST http://localhost:8080/schedule/default/example-forecast-aware-global/manual \
  -H "Content-Type: application/json" \
  -d '{
    "flavourWeights": {
      "precision-100": 0.4,
      "precision-50": 0.35,
      "precision-30": 0.25
    },
    "validUntil": "2025-11-09T15:30:00Z",
    "carbonForecastNow": 150,
    "carbonForecastNext": 220,
    "credits": {
      "balance": 0.3,
      "velocity": 0.05
    },
    "processing": {
      "throttle": 0.85,
      "ceilings": {
        "router": 8,
        "consumer": 12,
        "target": 15
      }
    }
  }'
```

The manual schedule will override automatic decisions until `validUntil` expires.

### Method 2: Mock Carbon API Server

Create a simple mock server that returns custom carbon intensity forecasts:

```bash
# Create a simple mock server (run in a separate terminal)
cat > mock-carbon-api.py << 'EOF'
from flask import Flask, jsonify
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# Define custom carbon intensity scenarios
SCENARIOS = {
    "rising": [150, 180, 220, 280, 320],  # Rising intensity (morning)
    "peak": [300, 320, 310, 305, 295],     # Peak intensity (midday)
    "falling": [250, 200, 150, 100, 80],   # Falling intensity (evening)
    "low": [50, 45, 40, 38, 35],           # Very low intensity (night)
    "volatile": [100, 250, 80, 300, 120],  # Volatile pattern
}

# Select scenario (change this to test different patterns)
ACTIVE_SCENARIO = "rising"

@app.route('/intensity/<start_time>/fw48h')
def get_forecast(start_time):
    """Return mock forecast schedule."""
    now = datetime.now(timezone.utc)
    data = []
    
    intensities = SCENARIOS[ACTIVE_SCENARIO]
    
    for i, intensity in enumerate(intensities * 10):  # Repeat pattern
        start = now + timedelta(minutes=30*i)
        end = start + timedelta(minutes=30)
        data.append({
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "intensity": {
                "forecast": intensity,
                "actual": intensity,
                "index": "moderate" if intensity < 200 else "high"
            }
        })
    
    return jsonify({"data": data})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
EOF

# Run the mock server
python3 mock-carbon-api.py
```

Then configure the decision engine to use the mock API:

```bash
# Update decision-engine deployment to use mock API
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=http://localhost:5000 \
  CARBON_API_CACHE_TTL=30

# Or edit the deployment directly
kubectl edit deployment decision-engine -n carbonshift
# Add under spec.template.spec.containers[0].env:
#   - name: CARBON_API_URL
#     value: "http://mock-carbon-api:5000"
#   - name: CARBON_API_CACHE_TTL
#     value: "30"
```

### Method 3: Custom Provider for Testing

Create a test-specific carbon provider that reads from a config file:

```bash
# Create a ConfigMap with test carbon intensity values
kubectl create configmap carbon-intensity-test -n carbonshift --from-literal=scenario='
{
  "schedule": [
    {"time": "00:00", "intensity": 50},
    {"time": "06:00", "intensity": 120},
    {"time": "09:00", "intensity": 250},
    {"time": "12:00", "intensity": 300},
    {"time": "15:00", "intensity": 200},
    {"time": "18:00", "intensity": 150},
    {"time": "21:00", "intensity": 80}
  ]
}'

# Mount the ConfigMap in the decision-engine pod
# (requires updating the deployment)
```

### Test Scenarios with Manual Overrides

#### Scenario A: Rising Carbon Intensity

```bash
# Morning scenario - intensity rising rapidly
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 120,
    "carbonForecastNext": 280,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Expected: Strategy should conserve credit (negative carbon_adjustment)
# Check: curl http://localhost:8080/schedule/default/test-schedule | jq .diagnostics
```

#### Scenario B: Falling Carbon Intensity

```bash
# Evening scenario - intensity falling rapidly
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 280,
    "carbonForecastNext": 120,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Expected: Strategy should spend credit (positive carbon_adjustment)
```

#### Scenario C: High Intensity Peak

```bash
# Midday peak - very high intensity
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 350,
    "carbonForecastNext": 340,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Expected: High baseline precision, low throttle, conservative approach
```

#### Scenario D: Very Clean Period

```bash
# Night scenario - very low intensity
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 40,
    "carbonForecastNext": 35,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Expected: Aggressive green strategy, high low-precision weight
```

### Automated Test Script

Create a script to cycle through scenarios:

```bash
#!/bin/bash
# test-carbon-scenarios.sh

DECISION_ENGINE="http://localhost:8080"
NAMESPACE="default"
SCHEDULE_NAME="test-schedule"

scenarios=(
  "rising:120:280:Should conserve credit"
  "falling:280:120:Should spend credit"
  "peak:350:340:High precision baseline"
  "clean:40:35:Aggressive green strategy"
  "stable:180:185:Balanced approach"
)

for scenario in "${scenarios[@]}"; do
  IFS=':' read -r name now next description <<< "$scenario"
  
  echo "========================================="
  echo "Testing scenario: $name"
  echo "Description: $description"
  echo "Carbon now: $now, next: $next"
  echo "========================================="
  
  # Set manual schedule
  valid_until=$(date -u -v+5M +%Y-%m-%dT%H:%M:%SZ)
  curl -s -X POST "$DECISION_ENGINE/schedule/$NAMESPACE/$SCHEDULE_NAME/manual" \
    -H "Content-Type: application/json" \
    -d "{
      \"carbonForecastNow\": $now,
      \"carbonForecastNext\": $next,
      \"validUntil\": \"$valid_until\"
    }" | jq .
  
  # Wait a moment for metrics to update
  sleep 2
  
  # Fetch and display results
  echo "Results:"
  curl -s "$DECISION_ENGINE/schedule/$NAMESPACE/$SCHEDULE_NAME" | \
    jq '{
      policy: .activePolicy,
      carbon: {now: .carbonForecastNow, next: .carbonForecastNext},
      adjustments: .diagnostics,
      weights: .flavours | map({name: .name, weight: .weight}),
      throttle: .processing.throttle
    }'
  
  echo ""
  echo "Press Enter to continue to next scenario..."
  read
done
```

## Performance Testing

```

### Load Test Script

```bash
#!/bin/bash
# load-test.sh

ENDPOINT="http://my-ml-service.default.svc.cluster.local"
DURATION=3600  # 1 hour
RPS=100

echo "Starting load test for $DURATION seconds at $RPS req/s"

vegeta attack -duration=${DURATION}s -rate=${RPS} -targets=<(echo "GET $ENDPOINT") | \
  vegeta report -type=text

# Analyze results
kubectl logs -n carbonshift deploy/decision-engine --tail=1000 | \
  grep "ForecastAwareGlobal" | \
  jq -r '.diagnostics | "\(.carbon_adjustment),\(.demand_adjustment),\(.total_adjustment)"' | \
  awk -F, '{
    carbon += $1; demand += $2; total += $3; count++
  } END {
    print "Avg carbon_adj:", carbon/count
    print "Avg demand_adj:", demand/count
    print "Avg total_adj:", total/count
  }'
```

## Next Steps

1. **Tune Weights**: Adjust the 0.35/0.25/0.25/0.15 weights based on your workload
2. **Extend Look-Ahead**: Increase forecast window from 6 to 12 points for longer planning
3. **Add Custom Metrics**: Export additional diagnostics for your specific use case
4. **Compare with Baseline**: Run A/B tests against simpler strategies

## References

- [Forecast-Aware-Global Strategy Documentation](../docs/forecast_aware_global_strategy.md)
- [Credit Scheduler Design](../docs/credit_scheduler.md)
- [Strategies Architecture](../decision-engine/scheduler/strategies/README.md)
