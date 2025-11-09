# Carbon Intensity Test Scenarios

This directory contains tools for testing the carbon-aware scheduler with controlled carbon intensity scenarios.

## Tools

### 1. Mock Carbon API (`mock-carbon-api.py`)

A Flask server that mimics the Carbon Intensity UK API with predefined test scenarios.

**Features:**
- Multiple predefined scenarios (rising, peak, falling, low, volatile, etc.)
- Runtime scenario switching via REST API
- Custom patterns from JSON files
- Compatible with Carbon Intensity API format

**Usage:**

```bash
# Start with a specific scenario
python3 mock-carbon-api.py --scenario rising

# Use custom pattern from file
python3 mock-carbon-api.py --scenario custom --file my-pattern.json

# Change scenario at runtime
curl -X POST http://localhost:5000/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak"}'

# Get current scenario
curl http://localhost:5000/scenario
```

**Available Scenarios:**
- `rising`: Morning pattern with increasing intensity (120 → 350 gCO2/kWh)
- `peak`: Midday peak with sustained high intensity (300-330 gCO2/kWh)
- `falling`: Evening pattern with decreasing intensity (280 → 60 gCO2/kWh)
- `low`: Night pattern with very low intensity (30-50 gCO2/kWh)
- `volatile`: Highly variable pattern for stress testing
- `stable`: Relatively constant intensity (~180 gCO2/kWh)
- `extreme-peak`: Very high spike (150 → 500 gCO2/kWh)
- `extreme-clean`: Very low carbon period (100 → 15 gCO2/kWh)

**Integration with Decision Engine:**

```bash
# Configure decision engine to use mock API
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=http://mock-carbon-api:5000 \
  CARBON_API_CACHE_TTL=30

# Or use port-forward for local testing
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080
# Then run mock API on localhost:5000
```

### 2. Scenario Test Script (`test-carbon-scenarios.sh`)

Automated bash script that tests multiple carbon intensity scenarios and validates scheduler behavior.

**Usage:**

```bash
# Ensure decision engine is accessible
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080

# Run all test scenarios
./test-carbon-scenarios.sh

# Run in CI mode (no interactive prompts)
CI=1 ./test-carbon-scenarios.sh

# Custom configuration
DECISION_ENGINE=http://localhost:8080 \
NAMESPACE=default \
SCHEDULE_NAME=my-schedule \
WAIT_TIME=10 \
./test-carbon-scenarios.sh
```

**Test Scenarios:**
1. **Rising**: 120 → 280 gCO2/kWh (expects negative carbon_adjustment)
2. **Falling**: 280 → 120 gCO2/kWh (expects positive carbon_adjustment)
3. **Peak**: 350 → 340 gCO2/kWh (expects conservative strategy)
4. **Clean**: 40 → 35 gCO2/kWh (expects aggressive green strategy)
5. **Stable**: 180 → 185 gCO2/kWh (expects neutral adjustment)
6. **Extreme Rise**: 100 → 400 gCO2/kWh (expects very negative adjustment)
7. **Extreme Fall**: 400 → 100 gCO2/kWh (expects very positive adjustment)

**Validation:**
- Checks carbon_adjustment values match expectations
- Displays flavour weight distribution
- Reports throttle factors and ceilings
- Provides pass/fail summary

### 3. Custom Pattern Files

Create JSON files with custom carbon intensity patterns:

**Example: `gradual-rise.json`**
```json
{
  "name": "Gradual Morning Rise",
  "description": "Slow increase over 12 hours",
  "pattern": [80, 95, 110, 130, 150, 175, 200, 230, 260, 290, 310, 320]
}
```

**Example: `daily-cycle.json`**
```json
{
  "name": "24-Hour Daily Cycle",
  "pattern": [
    40, 38, 35, 33, 35, 40,      // Night: 00:00-03:00
    50, 80, 120, 180, 250, 300,  // Morning: 03:00-09:00
    320, 330, 325, 315, 300, 280, // Midday: 09:00-15:00
    240, 200, 160, 120, 80, 60   // Evening: 15:00-21:00
  ]
}
```

**Usage:**
```bash
python3 mock-carbon-api.py --scenario custom --file daily-cycle.json
```

## Testing Workflow

### Complete Test Setup

1. **Deploy the Mock API in Kubernetes:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-carbon-api
  namespace: carbonshift
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-carbon-api
  template:
    metadata:
      labels:
        app: mock-carbon-api
    spec:
      containers:
      - name: api
        image: python:3.11-slim
        command: ["python3", "/app/mock-carbon-api.py"]
        ports:
        - containerPort: 5000
        volumeMounts:
        - name: script
          mountPath: /app
      volumes:
      - name: script
        configMap:
          name: mock-carbon-api-script
---
apiVersion: v1
kind: Service
metadata:
  name: mock-carbon-api
  namespace: carbonshift
spec:
  selector:
    app: mock-carbon-api
  ports:
  - port: 5000
    targetPort: 5000
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: mock-carbon-api-script
  namespace: carbonshift
data:
  mock-carbon-api.py: |
    # (paste the content of mock-carbon-api.py here)
```

2. **Configure Decision Engine:**

```bash
kubectl set env deployment/decision-engine -n carbonshift \
  CARBON_API_URL=http://mock-carbon-api:5000
```

3. **Run Tests:**

```bash
# Port-forward decision engine
kubectl port-forward -n carbonshift svc/decision-engine 8080:8080 &

# Run automated tests
./test-carbon-scenarios.sh

# Or use manual schedule overrides
curl -X POST http://localhost:8080/schedule/default/my-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 150,
    "carbonForecastNext": 220
  }'
```

## Manual Testing Examples

### Test Rising Carbon Intensity

```bash
# Set rising scenario in mock API
curl -X POST http://localhost:5000/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "rising"}'

# Wait for decision engine to fetch new data (check CARBON_API_CACHE_TTL)
sleep 30

# Check scheduler response
curl http://localhost:8080/schedule/default/my-schedule | jq .diagnostics
```

### Test Extreme Peak Event

```bash
# Simulate extreme peak
curl -X POST http://localhost:8080/schedule/default/my-schedule/manual \
  -H "Content-Type: application/json" \
  -d '{
    "carbonForecastNow": 500,
    "carbonForecastNext": 480
  }'

# Verify conservative behavior
curl http://localhost:8080/schedule/default/my-schedule | \
  jq '{throttle: .processing.throttle, weights: .flavours}'
```

### Test Very Clean Period

```bash
# Simulate very clean period
curl -X POST http://localhost:5000/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "extreme-clean"}'

# Verify aggressive green strategy
curl http://localhost:8080/schedule/default/my-schedule | \
  jq '.flavours[] | select(.name == "precision-30")'
```

## Metrics to Monitor

During testing, monitor these Prometheus metrics:

```promql
# Carbon adjustments
scheduler_credit_balance{policy="forecast-aware-global"}
rate(scheduler_policy_choice_total[5m])

# Throttling
scheduler_processing_throttle
scheduler_effective_replica_ceiling

# Forecasts
scheduler_forecast_intensity_timestamped
```

## Troubleshooting

**Mock API not accessible:**
```bash
# Check pod status
kubectl get pods -n carbonshift -l app=mock-carbon-api

# Check logs
kubectl logs -n carbonshift -l app=mock-carbon-api
```

**Decision engine not updating:**
```bash
# Check cache TTL
kubectl get deployment decision-engine -n carbonshift -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CARBON_API_CACHE_TTL")].value}'

# Force update by restarting
kubectl rollout restart deployment/decision-engine -n carbonshift
```

**Manual schedule expires too quickly:**
```bash
# Check validFor setting
curl http://localhost:8080/schedule/default/my-schedule | jq .validUntil
```

## Best Practices

1. **Use Mock API for Repeatable Tests**: Real Carbon Intensity data is unpredictable
2. **Start with Simple Scenarios**: Test rising/falling before complex patterns
3. **Monitor Metrics in Grafana**: Visual feedback helps validate behavior
4. **Run Automated Tests in CI**: Ensure changes don't break scheduler logic
5. **Document Custom Scenarios**: Keep JSON patterns with descriptions
6. **Test Edge Cases**: Extreme peaks, rapid changes, sustained periods

## Further Reading

- Carbon Intensity UK API: https://api.carbonintensity.org.uk/
- Decision Engine docs: `/decision-engine/README.md`
- TrafficSchedule CRD: `/operator/config/crd/`
