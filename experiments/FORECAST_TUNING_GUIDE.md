# Forecast-Aware Strategy: Tuning Guide

## Current Implementation (forecast_aware.py lines 31-34)

```python
if trend > 0:
    adjustment = -min(0.3, trend / max(forecast.intensity_now, 1e-6) * 0.5)
elif trend < 0:
    adjustment = min(0.3, abs(trend) / max(forecast.intensity_now, 1e-6) * 0.5)
```

## Key Tuning Parameters

### 1. **Adjustment Cap** (currently `0.3`)
   - **What it does**: Maximum absolute adjustment to allowance
   - **Current effect**: Limits forecast impact to ±30% of traffic
   - **Range to explore**: 0.1 to 1.0
   - **Impact**: Higher = stronger forecast influence

### 2. **Scaling Factor** (currently `0.5`)
   - **What it does**: Scales the trend/current ratio
   - **Current effect**: Moderates how aggressively we respond to trends
   - **Range to explore**: 0.2 to 2.0
   - **Impact**: Higher = more sensitive to forecast changes

## Why Current Settings Are Weak

**Problem**: The adjustment (±0.3 max) is too small compared to the carbon multiplier effect (0.5-2.0x)

**Example**:
- Base allowance: 0.4
- Carbon multiplier: 1.5 (current carbon = 225)
- → Base effect: 0.4 × 1.5 = 0.6
- Forecast adjustment: +0.2 (falling trend)
- → Final: 0.6 + 0.2 = 0.8

The base (0.6) dominates the adjustment (0.2).

## Tuning Strategies

### Strategy A: Increase Cap (Simple)
**Modify line 32 & 34**: Change `0.3` to `0.6` or `0.8`

**Pros**: Simple, direct
**Cons**: May cause instability if too high

### Strategy B: Increase Scaling Factor
**Modify line 32 & 34**: Change `* 0.5` to `* 1.0` or `* 1.5`

**Pros**: Makes forecast more sensitive to changes
**Cons**: Can overshoot with large deltas

### Strategy C: Increase Both (Balanced)
**Modify**: cap = 0.5, scaling = 0.8

**Pros**: Balanced approach
**Cons**: Two parameters to tune

### Strategy D: Make Multiplicative (Advanced)
Instead of adding adjustment, multiply the base result:
```python
final_multiplier = 1.0 + adjustment  # adjustment now in range [-0.5, +0.5]
weights = {k: v * final_multiplier for k, v in base.weights.items()}
```

**Pros**: Scales with base magnitude
**Cons**: More complex, harder to reason about

## Evaluation Metrics

### Primary Metric: **Forecast-Aware Swing**
```
swing = p100_usage(rising_trends) - p100_usage(falling_trends)
```
- Current: ~0.7pp (WEAK)
- Target: ≥10pp (MODERATE), ≥15pp (STRONG)

### Secondary Metrics:
1. **Overall carbon-aware swing** (low vs high current carbon)
   - Should remain ≥20pp (don't break base behavior)

2. **Credit balance stability**
   - Should stay within [-1.0, +1.0]
   - Unstable = oscillating wildly

3. **Average precision**
   - Should stay close to target (0.85 for TARGET_ERROR=0.15)
   - Too low = too much low-precision, credit debt
   - Too high = too cautious, not using carbon opportunities

4. **Forecast utilization rate**
   - % of samples where |adjustment| > 0.1
   - Higher = forecast is actively being used

## Recommended Tuning Process

### Phase 1: Quick Test (Choose One Setting)
```python
# Option A: Double the cap
adjustment = -min(0.6, trend / max(forecast.intensity_now, 1e-6) * 0.5)

# Option B: Double the scaling
adjustment = -min(0.3, trend / max(forecast.intensity_now, 1e-6) * 1.0)

# Option C: Moderate increase to both
adjustment = -min(0.5, trend / max(forecast.intensity_now, 1e-6) * 0.8)
```

Run ONE benchmark and check forecast-aware swing. If ≥10pp, move to Phase 2.

### Phase 2: Parameter Sweep (If you have time)
Test combinations:
- caps: [0.3, 0.5, 0.7, 1.0]
- scales: [0.5, 0.8, 1.0, 1.5]

Run 2-3 benchmarks per combination (16 combinations × 3 runs × 10 min = ~8 hours)

### Phase 3: Fine-tuning
Pick top 2-3 configurations from Phase 2.
Run longer benchmarks (20-30 min) to confirm stability.

## Expected Results

### Current (cap=0.3, scale=0.5):
- Forecast swing: ~0.7pp
- Overall swing: ~25pp
- Status: Forecast component adds minimal value

### Conservative Increase (cap=0.5, scale=0.8):
- Expected forecast swing: ~5-8pp
- Overall swing: ~25pp
- Status: Moderate forecast influence

### Aggressive Increase (cap=0.8, scale=1.2):
- Expected forecast swing: ~12-18pp
- Overall swing: ~22-28pp
- Status: Strong forecast influence
- Risk: Potential instability

### Very Aggressive (cap=1.0, scale=1.5):
- Expected forecast swing: ~20-25pp
- Overall swing: ~20-30pp
- Status: Forecast dominates
- Risk: High instability risk, may fight credit balance

## Trade-offs

**Increasing forecast influence:**
- ✅ Better anticipation of carbon changes
- ✅ Can "front-run" carbon spikes
- ✅ More interesting/novel contribution
- ❌ Risk of oscillation
- ❌ May conflict with credit balance
- ❌ Harder to reason about behavior

**Keeping forecast weak:**
- ✅ Stable behavior
- ✅ Credit balance remains dominant
- ✅ Easy to explain
- ❌ Forecast component adds little value
- ❌ Not much different from credit-greedy

## Recommendation

For a thesis demonstrating forecast-aware scheduling:

**Start with: cap=0.6, scale=1.0**
- Doubles the current influence
- Should show ~8-12pp forecast swing
- Still stable enough
- Clear demonstration of forecast value

If results are good, try: **cap=0.8, scale=1.2**
- Should show ~15-20pp forecast swing
- May need to monitor stability
- Strong demonstration of proactive behavior

## Implementation Steps

1. Edit `/decision-engine/scheduler/strategies/forecast_aware.py`
2. Change lines 32 & 34 parameters
3. Commit & push (triggers GitHub Actions build)
4. Wait 45 seconds, delete decision-engine pod
5. Run benchmark: `python3 run_simple_benchmark.py --policy forecast-aware`
6. Analyze with notebook
7. Compare forecast-aware swing to target (≥10pp)
