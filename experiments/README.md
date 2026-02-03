# Carbon-Aware Policy Benchmark

This directory contains tooling for reproducible experiments that compare the
three carbon-aware scheduling policies supported by the decision engine:

* `credit-greedy`
* `forecast-aware`
* `forecast-aware-global`

The goal is to run each policy under the same workload, with an identical
carbon-intensity scenario, and collect metrics for thesis analysis (graphs, tables, raw CSV/JSON data).

---

## Test Environment

All tests and experiments were conducted on:

- **Hardware:** MacBook Pro 14" (2021) with Apple M1 Pro (10-core CPU), 16 GB RAM
- **Kubernetes:** Docker Desktop Kubernetes cluster (8 vCPU, 12 GB RAM allocated)
- **OS:** macOS

---

## Prerequisites

1. A Kubernetes cluster with the carbonrouter stack already deployed
   (operator, decision-engine, router, consumer, RabbitMQ, carbonstat flavours).
2. A TrafficSchedule resource named `traffic-schedule` in namespace
   `carbonstat` (adjustable via CLI flags).
3. Local tools installed:
   * `kubectl`
   * Python 3.9+ with required packages (see below)

## Dependencies

Install required Python packages:

```bash
pip3 install --break-system-packages requests prometheus_client flask locust matplotlib
```

---

## Quick Start

**Verify prerequisites:**

```bash
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 preflight_check.py
```

This checks:
* CLI tools (kubectl, python3, locust)
* Python packages (requests, prometheus_client, flask, locust, matplotlib)
* Experiment files
* Kubernetes resources (TrafficSchedule, deployments)
* Port-forwards (18000-18003)
* Mock carbon API (localhost:5001)
* Decision engine configuration (CARBON_API_URL)

**Complete benchmark run:**

```bash
# 1. Start port-forwards (in a separate terminal, or use tmux/screen)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
./setup_portforwards.sh

# 2. Start mock carbon API (in another terminal)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 mock-carbon-api.py --step-minutes 1 --data carbon_scenario.json

# 3. Run the simple benchmark (all policies)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 run_simple_benchmark.py

# OR run the autoscaling benchmark (with throttling experiments)
python3 run_autoscaling_benchmark.py

# 4. Analyze results using the Jupyter notebooks in this folder
```

---


## Files Overview

### Benchmark Scripts

- **run_simple_benchmark.py** - Main benchmark script for testing carbon-aware policies
- **run_autoscaling_benchmark.py** - Benchmark script for autoscaling/throttling experiments
- **preflight_check.py** - Validates prerequisites before running benchmarks
- **setup_portforwards.sh** - Sets up all required port-forwards with verification

### Test Tools

- **mock-carbon-api.py** - Flask server that mimics the Carbon Intensity UK API
- **test-carbon-scenarios.sh** - Automated script that tests multiple carbon intensity scenarios
- **quick-test.sh** - Interactive script for quick manual testing
- **scenarios/** - Predefined carbon intensity pattern files (JSON)

### Configuration Files

- **carbon_scenario.json** - Carbon intensity pattern for simple benchmarks
- **carbon_scenario_autoscaling.json** - Extended carbon intensity pattern for autoscaling benchmarks
- **demand_scenario.json** - Demand/load pattern for benchmarks
- **power_profiles.json** - Power consumption profiles for different precision levels

### Load Testing

- **locust_router.py** - Locust workload generator for router endpoint
- **locust_ramping.py** - Locust workload generator with ramping load patterns

### Jupyter Notebooks (Analysis)

- **credit_greedy_analysis.ipynb** - Analysis of credit-greedy strategy results
- **forecast_aware_analysis_2.ipynb** - Analysis of forecast-aware strategy results
- **forecast_aware_global_analysis.ipynb** - Analysis of forecast-aware-global strategy results
- **autoscaling_analysis.ipynb** - Analysis of autoscaling/throttling experiments
- **ultimate_strategy_showdown.ipynb** - Comparative analysis of all strategies

---

## Results Structure

After running the benchmark, results are saved in `results/`:

```
results/
├── simple_YYYYMMDD_HHMMSS/
│   ├── credit-greedy/
│   │   ├── timeseries.csv              # Periodic samples (precision, credits, carbon, etc.)
│   │   ├── summary.json                # Test summary (total requests, mean precision, etc.)
│   │   ├── schedule_before.json        # TrafficSchedule state before test
│   │   └── schedule_after.json         # TrafficSchedule state after test
│   ├── forecast-aware/
│   │   └── ... (same structure)
│   └── forecast-aware-global/
│       └── ... (same structure)
└── autoscaling_YYYYMMDD_HHMMSS/
    ├── forecast-aware-global-with-throttle/
    │   └── ... (same structure)
    └── forecast-aware-global-no-throttle/
        └── ... (same structure)
```

---

## Detailed Workflow

### 1. Port Forwarding

The `setup_portforwards.sh` script establishes and verifies all required port-forwards:

- **18000**: Router endpoint (for Locust traffic)
- **18001**: Router metrics (Prometheus format)
- **18002**: Consumer metrics (Prometheus format)
- **18003**: Decision engine metrics (Prometheus format)

Run it in a separate terminal and leave it running:

```bash
./setup_portforwards.sh
```

### 2. Mock Carbon API

The mock API provides deterministic carbon intensity forecasts. The patterns
in `carbon_scenario.json` and `carbon_scenario_autoscaling.json` simulate
realistic variations:

- Morning rise: 220 → 290 gCO₂/kWh
- Midday peak: 290 → 100 gCO₂/kWh (sharp drop)
- Evening stability: 100 → 60 gCO₂/kWh (clean energy)
- Night volatility: 60 → 320 → 50 gCO₂/kWh

Start it with 1-minute steps to match the test duration:

```bash
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 mock-carbon-api.py --step-minutes 1 --data carbon_scenario.json
```

**Important**: The decision engine must be configured to use this mock API.
Set the environment variable:

```bash
kubectl set env deployment/carbonrouter-decision-engine \
  -n carbonrouter-system \
  CARBON_API_URL=http://host.docker.internal:5001
```

(Or use `http://carbon-api.carbonstat.svc.cluster.local:5001` if deployed in-cluster)

### 3. Running the Benchmark

**Simple Benchmark (policy comparison):**

```bash
python3 run_simple_benchmark.py
```

This tests all three policies (credit-greedy, forecast-aware, forecast-aware-global)
under the same conditions and collects metrics for comparison.

**Autoscaling Benchmark (throttling experiments):**

```bash
python3 run_autoscaling_benchmark.py
```

This tests the forecast-aware-global policy with and without autoscaling throttling
to measure the impact on carbon emissions and queue depth.

### 4. Analyzing Results

After the benchmark completes, use the Jupyter notebooks to analyze results:

```bash
# Start Jupyter
jupyter notebook

# Open one of the analysis notebooks:
# - credit_greedy_analysis.ipynb
# - forecast_aware_analysis_2.ipynb
# - forecast_aware_global_analysis.ipynb
# - autoscaling_analysis.ipynb
# - ultimate_strategy_showdown.ipynb
```

The notebooks generate visualizations including:
- Precision over time
- Carbon intensity correlation
- Credit balance dynamics
- Request distribution by precision level
- Comparative bar charts and radar plots

---

## Mock Carbon API

The `mock-carbon-api.py` is a Flask server that mimics the Carbon Intensity UK API with predefined test scenarios.

### Features

- Multiple predefined scenarios (rising, peak, falling, low, volatile, etc.)
- Runtime scenario switching via REST API
- Custom patterns from JSON files
- Compatible with Carbon Intensity API format

### Usage

```bash
# Start with a specific scenario
python3 mock-carbon-api.py --scenario rising

# Use custom pattern from file
python3 mock-carbon-api.py --scenario custom --file my-pattern.json

# Change scenario at runtime
curl -X POST http://localhost:5001/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak"}'

# Get current scenario
curl http://localhost:5001/scenario
```

### Available Scenarios

- `rising`: Morning pattern with increasing intensity (120 → 350 gCO2/kWh)
- `peak`: Midday peak with sustained high intensity (300-330 gCO2/kWh)
- `falling`: Evening pattern with decreasing intensity (280 → 60 gCO2/kWh)
- `low`: Night pattern with very low intensity (30-50 gCO2/kWh)
- `volatile`: Highly variable pattern for stress testing
- `stable`: Relatively constant intensity (~180 gCO2/kWh)
- `extreme-peak`: Very high spike (150 → 500 gCO2/kWh)
- `extreme-clean`: Very low carbon period (100 → 15 gCO2/kWh)

### Integration with Decision Engine

```bash
# Configure decision engine to use mock API
kubectl set env deployment/carbonrouter-decision-engine -n carbonrouter-system \
  CARBON_API_URL=http://mock-carbon-api:5001 \
  CARBON_API_CACHE_TTL=30

# Or use port-forward for local testing
kubectl port-forward -n carbonrouter-system svc/decision-engine 8080:8080
# Then run mock API on localhost:5001
```

---

## Test Scripts

### Scenario Test Script (`test-carbon-scenarios.sh`)

Automated bash script that tests multiple carbon intensity scenarios and validates scheduler behavior.

```bash
# Ensure decision engine is accessible
kubectl port-forward -n carbonrouter-system svc/decision-engine 8080:8080

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

---

## Custom Pattern Files

Create JSON files with custom carbon intensity patterns in the `scenarios/` directory:

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
    40, 38, 35, 33, 35, 40,
    50, 80, 120, 180, 250, 300,
    320, 330, 325, 315, 300, 280,
    240, 200, 160, 120, 80, 60
  ]
}
```

**Usage:**

```bash
python3 mock-carbon-api.py --scenario custom --file scenarios/daily-cycle.json
```

---

## Manual Testing Examples

### Test Rising Carbon Intensity

```bash
# Set rising scenario in mock API
curl -X POST http://localhost:5001/scenario \
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
curl -X POST http://localhost:5001/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario": "extreme-clean"}'

# Verify aggressive green strategy
curl http://localhost:8080/schedule/default/my-schedule | \
  jq '.flavours[] | select(.name == "precision-30")'
```

---

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

---

## Troubleshooting

**Mock API not accessible:**

```bash
# Check if running locally
curl http://localhost:5001/health

# If deployed in-cluster, check pod status
kubectl get pods -n carbonrouter-system -l app=mock-carbon-api
kubectl logs -n carbonrouter-system -l app=mock-carbon-api
```

**Decision engine not updating:**

```bash
# Check cache TTL
kubectl get deployment carbonrouter-decision-engine -n carbonrouter-system \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CARBON_API_CACHE_TTL")].value}'

# Force update by restarting
kubectl rollout restart deployment/carbonrouter-decision-engine -n carbonrouter-system
```

**Manual schedule expires too quickly:**

```bash
# Check validFor setting
curl http://localhost:8080/schedule/default/my-schedule | jq .validUntil
```

---

## Further Reading

- Carbon Intensity UK API: <https://api.carbonintensity.org.uk/>
- Decision Engine docs: `/decision-engine/README.md`
- TrafficSchedule CRD: `/operator/config/crd/`
