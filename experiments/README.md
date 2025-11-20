# Carbon-Aware Policy Benchmark

This directory contains tooling for reproducible experiments that compare the
three carbon-aware scheduling policies supported by the decision engine:

* `credit-greedy`
* `forecast-aware`
* `forecast-aware-global`

The goal is to run each policy under the same workload, with an identical
carbon-intensity scenario, and collect metrics for thesis analysis (graphs, tables, raw CSV/JSON data).

> **📋 New to carbon scenarios?** See [CARBON_SETUP_SUMMARY.md](CARBON_SETUP_SUMMARY.md) for a quick guide on using `carbon_scenario.json` with the mock Carbon API.

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
* Mock carbon API (localhost:5000)
* Decision engine configuration (CARBON_API_URL)

**Complete benchmark run (30 minutes total - 10 minutes per policy):**

```bash
# 1. Start port-forwards (in a separate terminal, or use tmux/screen)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
./setup_portforwards.sh

# 2. Start mock carbon API (in another terminal)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/tests
python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json

# 3. Run the temporal benchmark (all policies)
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
python3 run_temporal_benchmark.py

# OR run a single policy (10 minutes)
python3 run_temporal_benchmark.py --policy credit-greedy

# 4. Generate graphs
python3 plot_results.py

# 5. View plots
open plots/
```

---


## Files Overview

- **carbon_scenario.json** - 180-point carbon intensity pattern (1-minute intervals, covers 3+ hours for forecast-aware-global)
- **locust_router.py** - Locust workload generator for router endpoint
- **run_temporal_benchmark.py** - Main orchestration script (tests all 3 carbon-aware policies with 10-minute runs each)
- **setup_portforwards.sh** - Sets up all required port-forwards with verification
- **plot_results.py** - Generates comprehensive graphs from benchmark results
- **analyze_results.py** - Post-processing script for timeseries analysis (optional)

---

## Results Structure

After running the benchmark, results are saved in `results/`:

```
results/
├── credit-greedy/
│   ├── timeseries.csv              # Periodic samples (precision, credits, carbon, etc.)
│   ├── summary.json                # Test summary (total requests, mean precision, etc.)
│   ├── schedule_before.json        # TrafficSchedule state before test
│   ├── schedule_after.json         # TrafficSchedule state after test
│   ├── router_metrics_baseline.txt # Prometheus metrics at start
│   ├── router_metrics_final.txt    # Prometheus metrics at end
│   ├── consumer_metrics_final.txt  # Consumer metrics
│   └── engine_metrics_final.txt    # Decision engine metrics
├── forecast-aware/
│   └── ... (same structure)
└── forecast-aware-global/
    └── ... (same structure)
```

Plots are saved in `plots/`:

```
plots/
├── credit-greedy/
│   ├── precision.png       # Precision over time
│   ├── carbon.png          # Carbon intensity over time
│   ├── credits.png         # Credit balance over time
│   └── request_rate.png    # Request rate over time
├── forecast-aware/
│   └── ... (same structure)
├── forecast-aware-global/
│   └── ... (same structure)
└── comparison/
    ├── precision_comparison.png   # All policies on one graph
    ├── credits_comparison.png     # Credit balance comparison
    └── summary_comparison.png     # Bar charts (mean precision, total requests, carbon)
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

The mock API provides deterministic carbon intensity forecasts. The 180-point
pattern in `carbon_scenario.json` simulates realistic variations over 3+ hours:

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
  CARBON_API_URL=http://host.docker.internal:5000
```

(Or use `http://carbon-api.carbonstat.svc.cluster.local:5000` if deployed in-cluster)

### 3. Running the Benchmark

The `run_temporal_benchmark.py` script orchestrates the complete test.

**Run all policies (30 minutes):**

```bash
python3 run_temporal_benchmark.py
```

**Run a single policy (10 minutes):**

```bash
# Test only credit-greedy
python3 run_temporal_benchmark.py --policy credit-greedy

# Test only forecast-aware
python3 run_temporal_benchmark.py --policy forecast-aware

# Test only forecast-aware-global
python3 run_temporal_benchmark.py --policy forecast-aware-global
```

**Run multiple specific policies (20 minutes):**

```bash
python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware
```

**For each selected policy, the script:**
1. Patch TrafficSchedule to use the policy
2. Restart decision engine to reset credits
3. Restart router to reset metrics
4. Reset mock carbon API to start from beginning
5. Collect baseline metrics
6. Launch Locust workload (150 users, 50/s spawn rate, 10 minutes)
7. Sample metrics every 30 seconds during the test
8. Collect final metrics and compute deltas
9. Save all data to `results/<policy>/`

Run the benchmark (takes ~30 minutes total):

```bash
python3 run_temporal_benchmark.py
```

The script will print progress updates:

```
============================================================
Temporal Policy Benchmark
============================================================

Found 3 policies to test: credit-greedy, forecast-aware, forecast-aware-global
Test duration: 10.0 minutes per policy
Total estimated time: 30.0 minutes

Testing policy: credit-greedy (1/3)
  ⏳ Collecting baseline...
  ✓ Baseline collected (starting from 0 requests)
  🚀 Starting Locust (150 users, 50/s spawn rate)...
  📊 Sampling every 30s...
    Sample 5: 2847 req/period, prec=0.852, credits=145.23
    ...
  ✓ Collected 20 samples
  ✓ Final metrics collected (total delta: 11388 requests)

  Results:
    Duration: 10.0 minutes
    Samples: 20
    Total requests: 11388
    Mean precision: 0.847
    Mean carbon intensity: 189.3 gCO₂/kWh
    Final credit balance: 142.56

...
```

### 4. Generating Graphs

After the benchmark completes, generate visualization plots:

```bash
python3 plot_results.py
```

This creates:
- **Individual plots** for each policy (4 plots per policy)
- **Comparison plots** showing all policies together
- **Summary bar charts** for mean values

View the results:

```bash
open plots/
```

---
