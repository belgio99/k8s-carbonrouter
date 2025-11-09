# Quick Reference: Manual Carbon Intensity Testing

This cheat sheet provides copy-paste ready commands for testing the carbon-aware scheduler with manual carbon intensity values.

## Prerequisites

```bash
# Port-forward the decision engine
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080
```

## Method 1: Manual Schedule Override (Fastest)

### Set Manual Carbon Intensity

```bash
# Rising intensity scenario (morning)
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 120,
    "carbonForecastNext": 280,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Falling intensity scenario (evening)
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 280,
    "carbonForecastNext": 120,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Peak intensity scenario (midday)
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 350,
    "carbonForecastNext": 340,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# Clean period scenario (night)
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 40,
    "carbonForecastNext": 35,
    "validUntil": "'$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ)'"
  }'
```

### View Current Schedule

```bash
# Get full schedule
curl http://localhost:8080/schedule/default/test-schedule | jq

# Get just diagnostics
curl http://localhost:8080/schedule/default/test-schedule | jq .diagnostics

# Get flavour weights
curl http://localhost:8080/schedule/default/test-schedule | jq '.flavours[] | {name, weight}'

# Get carbon adjustments
curl http://localhost:8080/schedule/default/test-schedule | \
  jq '{
    carbon_adjustment: .diagnostics.carbon_adjustment,
    demand_adjustment: .diagnostics.demand_adjustment,
    total_adjustment: .diagnostics.total_adjustment
  }'
```

## Method 2: Mock Carbon API Server

### Start Mock API

```bash
# Terminal 1: Start mock API
cd tests
python3 mock-carbon-api.py --scenario rising

# Or use specific scenario
python3 mock-carbon-api.py --scenario peak
python3 mock-carbon-api.py --scenario falling
python3 mock-carbon-api.py --scenario low
python3 mock-carbon-api.py --scenario volatile

# Or use custom pattern file
python3 mock-carbon-api.py --scenario custom --file scenarios/uk-daily-pattern.json
```

### Change Scenario at Runtime

```bash
# Change to different scenario without restarting
curl -X POST http://localhost:5000/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak"}'

# View current scenario
curl http://localhost:5000/scenario | jq
```

### Configure Decision Engine to Use Mock API

```bash
# Temporary (pod restart resets)
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=http://mock-carbon-api:5000 \
  CARBON_API_CACHE_TTL=30

# Or with local port-forward
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=http://host.docker.internal:5000 \
  CARBON_API_CACHE_TTL=30
```

## Method 3: Automated Test Scripts

### Run All Scenarios

```bash
cd tests
./test-carbon-scenarios.sh
```

### Quick Interactive Menu

```bash
cd tests
./quick-test.sh
```

## Common Test Scenarios

### Test 1: Rising Carbon Intensity (Expects Credit Conservation)

```bash
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 150,
    "carbonForecastNext": 300
  }'

# Wait 3 seconds
sleep 3

# Check result - expect negative carbon_adjustment
curl http://localhost:8080/schedule/default/test-schedule | \
  jq '.diagnostics.carbon_adjustment'
```

### Test 2: Falling Carbon Intensity (Expects Credit Spending)

```bash
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 300,
    "carbonForecastNext": 150
  }'

sleep 3

# Check result - expect positive carbon_adjustment
curl http://localhost:8080/schedule/default/test-schedule | \
  jq '.diagnostics.carbon_adjustment'
```

### Test 3: Extreme Peak (Expects Conservative Strategy)

```bash
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 500,
    "carbonForecastNext": 480
  }'

sleep 3

# Check result - expect high baseline weight, low throttle
curl http://localhost:8080/schedule/default/test-schedule | \
  jq '{
    baseline_weight: (.flavours[] | select(.name == "precision-100") | .weight),
    throttle: .processing.throttle
  }'
```

### Test 4: Very Clean Period (Expects Aggressive Green)

```bash
curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 30,
    "carbonForecastNext": 25
  }'

sleep 3

# Check result - expect high low-precision weight
curl http://localhost:8080/schedule/default/test-schedule | \
  jq '{
    low_precision_weight: (.flavours[] | select(.name == "precision-30") | .weight),
    total_adjustment: .diagnostics.total_adjustment
  }'
```

## Monitor Results with Prometheus

```bash
# Credit balance
curl http://localhost:8001/metrics | grep scheduler_credit_balance

# Carbon adjustments
curl http://localhost:8001/metrics | grep scheduler_forecast_intensity

# Throttle factor
curl http://localhost:8001/metrics | grep scheduler_processing_throttle

# Replica ceilings
curl http://localhost:8001/metrics | grep scheduler_effective_replica_ceiling
```

## Watch Schedule Changes in Real-Time

```bash
# Watch schedule updates
watch -n 2 'curl -s http://localhost:8080/schedule/default/test-schedule | jq "{
  carbon: {now: .carbonForecastNow, next: .carbonForecastNext},
  adjustment: .diagnostics.carbon_adjustment,
  weights: .flavours | map({name, weight: (.weight * 100 | round)})
}"'
```

## Kubernetes TrafficSchedule Status

```bash
# View TrafficSchedule status
kubectl get trafficschedule test-schedule -o yaml

# Watch for changes
kubectl get trafficschedule test-schedule -w

# Check specific fields
kubectl get trafficschedule test-schedule \
  -o jsonpath='{.status.flavours}' | jq
```

## Cleanup

```bash
# Remove manual override (will resume automatic scheduling)
# Just wait for validUntil to expire, or restart decision-engine

# Reset decision engine to use real Carbon Intensity API
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=https://api.carbonintensity.org.uk
```

## Troubleshooting

### Schedule not updating?

```bash
# Check decision engine logs
kubectl logs -n carbonshift deployment/decision-engine --tail=50

# Check if manual override is still active
curl http://localhost:8080/schedule/default/test-schedule | jq .validUntil
```

### Mock API not working?

```bash
# Test mock API directly
curl http://localhost:5000/intensity/$(date -u +%Y-%m-%dT%H:%M:%SZ)/fw48h | jq

# Check current scenario
curl http://localhost:5000/scenario | jq
```

### Metrics not appearing?

```bash
# Check Prometheus metrics endpoint
curl http://localhost:8001/metrics | grep scheduler

# Check if metrics port is correct
kubectl get deployment decision-engine -n carbonshift \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="METRICS_PORT")].value}'
```

## Tips

1. **validUntil**: Manual overrides expire. Set far in future for extended testing:
   ```bash
   "validUntil": "2025-12-31T23:59:59Z"
   ```

2. **Multiple schedules**: Test different schedules simultaneously by using different names:
   ```bash
   curl -X POST http://localhost:8080/schedule/default/schedule-A/manual ...
   curl -X POST http://localhost:8080/schedule/default/schedule-B/manual ...
   ```

3. **Baseline comparison**: Always test with carbon intensity = 0 to see strategy behavior:
   ```bash
   curl -X POST http://localhost:8080/schedule/default/test-schedule/manual \
     -d '{"carbonForecastNow": 0, "carbonForecastNext": 0}'
   ```

4. **Grafana**: Import dashboard from `grafana/trafficschedule-dashboard.json` for visual monitoring

5. **Load testing**: Combine with Locust to see real impact:
   ```bash
   locust -f tests/locust/locustfile.py --host http://router-service
   ```
