# Quick Reference - Temporal Benchmark

## Individual Policy Tests (10 minutes each)

```bash
# Test credit-greedy only
python3 run_temporal_benchmark.py --policy credit-greedy

# Test forecast-aware only
python3 run_temporal_benchmark.py --policy forecast-aware

# Test forecast-aware-global only
python3 run_temporal_benchmark.py --policy forecast-aware-global
```

## Multiple Policies

```bash
# Test two policies (20 minutes)
python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware

# Test all three carbon-aware policies (30 minutes)
python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware --policy forecast-aware-global
```

## All Policies (30 minutes)

```bash
python3 run_temporal_benchmark.py
```

## Custom Output Directory

```bash
# Save results to a specific directory
python3 run_temporal_benchmark.py --policy credit-greedy --output-dir my_test_results
```

## Complete Workflow

### Prerequisites

```bash
# 1. Verify everything is ready
python3 preflight_check.py
```

### Terminal 1: Port Forwards

```bash
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments
./setup_portforwards.sh
```

### Terminal 2: Mock Carbon API

```bash
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/tests
python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json
```

### Terminal 3: Run Benchmark

```bash
cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments

# Option A: Single policy (10 min)
python3 run_temporal_benchmark.py --policy credit-greedy

# Option B: Multiple policies (20 min)
python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware

# Option C: All policies (30 min)
python3 run_temporal_benchmark.py
```

### Generate Graphs

```bash
python3 plot_results.py
open plots/
```

## Results Location

Results are saved to `results/<timestamp>/` with subdirectories for each tested policy:

```
results/20251109_143022/
├── credit-greedy/
│   ├── timeseries.csv
│   ├── summary.json
│   └── ...
├── forecast-aware/
│   └── ...
└── benchmark_summary.json
```

Plots are saved to `plots/` with subdirectories for each policy:

```
plots/
├── credit-greedy/
│   ├── precision.png
│   ├── carbon.png
│   ├── credits.png
│   └── request_rate.png
├── forecast-aware/
│   └── ...
└── comparison/
    ├── precision_comparison.png
    ├── credits_comparison.png
    └── summary_comparison.png
```

## Troubleshooting

### Port-forwards not responding

```bash
# Kill existing port-forwards
pkill -f "kubectl port-forward"

# Restart
./setup_portforwards.sh
```

### Mock API not accessible

```bash
# Check if running
curl http://localhost:5000/scenario

# Restart if needed
cd ../tests
python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json
```

### Decision engine not using mock API

```bash
# Set CARBON_API_URL
kubectl set env deployment/carbonrouter-decision-engine \
  -n carbonrouter-system \
  CARBON_API_URL=http://host.docker.internal:5000

# Wait for rollout
kubectl rollout status deployment/carbonrouter-decision-engine -n carbonrouter-system
```

## Time Estimates

| Command | Duration | Description |
|---------|----------|-------------|
| `--policy credit-greedy` | 10 min | Single policy test |
| `--policy X --policy Y` | 20 min | Two policies |
| `--policy X --policy Y --policy Z` | 30 min | Three policies |
| No arguments (all 3) | 30 min | Complete benchmark |
| `plot_results.py` | ~30 sec | Generate all graphs |
