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

## Performance Testing

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
