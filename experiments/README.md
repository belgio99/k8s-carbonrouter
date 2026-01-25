# Carbon-Aware Policy Benchmark

This directory contains tooling for reproducible experiments that compare the
three carbon-aware scheduling policies supported by the decision engine:

* `credit-greedy`
* `forecast-aware`
* `forecast-aware-global`

The goal is to run each policy under the same workload, with an identical
carbon-intensity scenario, and collect metrics for thesis analysis (graphs, tables, raw CSV/JSON data).

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
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/tests
python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json

# 3. Run the simple benchmark (all policies)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 run_simple_benchmark.py

# OR run the autoscaling benchmark (with throttling experiments)
python3 run_autoscaling_benchmark.py

# 4. Analyze results using the Jupyter notebooks in this folder
```

---


## Files Overview

- **carbon_scenario.json** - Carbon intensity pattern for simple benchmarks
- **carbon_scenario_autoscaling.json** - Extended carbon intensity pattern for autoscaling benchmarks
- **demand_scenario.json** - Demand/load pattern for benchmarks
- **power_profiles.json** - Power consumption profiles for different precision levels
- **locust_router.py** - Locust workload generator for router endpoint
- **locust_ramping.py** - Locust workload generator with ramping load patterns
- **run_simple_benchmark.py** - Main benchmark script for testing carbon-aware policies
- **run_autoscaling_benchmark.py** - Benchmark script for autoscaling/throttling experiments
- **preflight_check.py** - Validates prerequisites before running benchmarks
- **setup_portforwards.sh** - Sets up all required port-forwards with verification

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
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/tests
python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json
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
