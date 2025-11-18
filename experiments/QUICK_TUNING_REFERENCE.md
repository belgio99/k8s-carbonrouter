# Quick Reference: Tuning Forecast-Aware Parameters

## TL;DR

**Current Problem**: Forecast adjustment (~±0.3 max) is too weak compared to carbon multiplier (0.5-2.0x).
**Result**: Forecast-aware behaves ~95% like credit-greedy, only ~5% from forecast.
**Goal**: Increase forecast influence to show ≥10-15pp swing between rising vs falling forecasts.

## One-Command Parameter Test

```bash
# See what different parameters would achieve WITHOUT running benchmarks
python3 forecast_parameter_simulator.py
```

This simulates 25 parameter combinations and recommends the best ones to try.

## Quick Fix (Recommended Starting Point)

### Option 1: Conservative (cap=0.5, scale=1.0)
**File**: `/decision-engine/scheduler/strategies/forecast_aware.py`
**Lines 32 & 34**: Change from:
```python
adjustment = -min(0.3, trend / max(forecast.intensity_now, 1e-6) * 0.5)
```
To:
```python
adjustment = -min(0.5, trend / max(forecast.intensity_now, 1e-6) * 1.0)
```

**Expected**: ~8-12pp forecast swing (MODERATE)

### Option 2: Moderate (cap=0.6, scale=1.2)
```python
adjustment = -min(0.6, trend / max(forecast.intensity_now, 1e-6) * 1.2)
```

**Expected**: ~12-16pp forecast swing (MODERATE-STRONG)

### Option 3: Aggressive (cap=0.8, scale=1.5)
```python
adjustment = -min(0.8, trend / max(forecast.intensity_now, 1e-6) * 1.5)
```

**Expected**: ~18-25pp forecast swing (STRONG)
**Risk**: May cause instability

## Deployment Steps

```bash
# 1. Edit the file
vi /Users/belgio/git-repos/k8s-carbonaware-scheduler/decision-engine/scheduler/strategies/forecast_aware.py

# 2. Commit and push
git add decision-engine/scheduler/strategies/forecast_aware.py
git commit -m "tune: increase forecast adjustment cap to X and scale to Y"
git push

# 3. Wait for GitHub Actions (45 seconds)
sleep 45

# 4. Delete pod to pull new image
kubectl delete pod -n carbonrouter-system -l app=carbonrouter-decision-engine

# 5. Wait for new pod
sleep 10
kubectl get pods -n carbonrouter-system | grep decision-engine

# 6. Run benchmark
cd experiments
python3 run_simple_benchmark.py --policy forecast-aware

# 7. Analyze
# Open forecast_aware_analysis.ipynb and look at Section 5:
# "Forecast Impact Analysis" → "KEY INSIGHT" section
# Check the "p100 swing between RISING vs FALLING forecasts" value
```

## Success Criteria

| Forecast Swing | Status | Meaning |
|----------------|--------|---------|
| 0-5pp | WEAK ❌ | Forecast barely matters |
| 5-10pp | FAIR ⚠️ | Forecast has minor influence |
| 10-15pp | MODERATE ✓ | Forecast clearly influences behavior |
| 15-20pp | STRONG ✅ | Forecast significantly affects decisions |
| 20+pp | VERY STRONG ✅✅ | Forecast dominates (watch stability!) |

## What to Watch For

### Good Signs ✅
- Forecast swing increases to ≥10pp
- Overall carbon-aware swing stays ≥20pp
- Credit balance stays within [-1.0, +1.0]
- Average precision near target (~0.85)

### Warning Signs ⚠️
- Credit balance oscillating wildly
- Average precision dropping below 0.70
- P100 usage showing extreme spikes

### Bad Signs ❌
- System becomes unstable
- Forecast swing > 30pp (too aggressive)
- Credit balance constantly hitting limits

## Iterative Approach

1. **Run simulator**: `python3 forecast_parameter_simulator.py`
2. **Pick top candidate** from recommendations
3. **Test once**: Run one 10-min benchmark
4. **Evaluate**: Check forecast swing in notebook
5. **Adjust**:
   - If <10pp → increase parameters
   - If 10-20pp → good, test stability with longer run
   - If >25pp → decrease parameters
6. **Repeat** until satisfied

## Example Session

```bash
# Step 1: See recommendations
python3 forecast_parameter_simulator.py
# Output: "RECOMMENDED START: cap=0.6, scale=1.0, Expected: +12.3pp"

# Step 2: Make the change
# Edit forecast_aware.py, set cap=0.6, scale=1.0

# Step 3: Deploy
git commit -am "tune: forecast cap=0.6, scale=1.0"
git push
sleep 45
kubectl delete pod -n carbonrouter-system -l app=carbonrouter-decision-engine

# Step 4: Test
python3 run_simple_benchmark.py --policy forecast-aware

# Step 5: Check results
# In notebook, Section 5 shows: "p100 swing: +14.2pp" → STRONG ✅
# Decision: Keep these parameters!
```

## Time Estimates

- **Simulator**: ~5 seconds
- **Code change**: ~2 minutes
- **Deploy**: ~1 minute (commit + build + pod restart)
- **Benchmark**: ~10 minutes
- **Analysis**: ~2 minutes

**Total per iteration**: ~15 minutes
**Recommended iterations**: 2-3

So you can find good parameters in **30-45 minutes total**.
