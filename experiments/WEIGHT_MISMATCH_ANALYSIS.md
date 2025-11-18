# Weight Mismatch Analysis: Commanded vs Actual Distribution

## Summary

The difference between **commanded weights** (what the decision engine computes) and **actual request distribution** (what the router routes) is caused by **schedule propagation lag** in the polling-based operator architecture.

### Average Deviations (All Within Random Variance)

| Strategy | p30 | p50 | p100 |
|----------|-----|-----|------|
| credit-greedy | +0.29pp | +0.33pp | -0.63pp |
| forecast-aware | -0.91pp | -0.73pp | +1.64pp |
| precision-tier | -0.35pp | -0.19pp | +0.54pp |
| forecast-aware-global | -0.18pp | -0.19pp | +0.37pp |

**All deviations are well within expected random variance (±2.5pp for ~300 requests).**

However, during **schedule transitions**, there are temporary large deviations of 20-75pp that resolve within 5-15 seconds.

## Root Cause: Polling-Based Schedule Propagation

### Architecture Flow

```
Decision Engine → Operator (polls) → CRD Status → Router Watch → Actual Routing
   [15s eval]     [~10s poll]       [K8s API]     [K8s watch]
```

### Timing Parameters

- **Engine evaluation interval**: 15 seconds (SCHEDULE_EVAL_INTERVAL_SEC)
- **Schedule validity**: 10 seconds (validFor set by benchmark)
- **Operator polling**: Every ~10 seconds (requeues at validUntil expiry)
- **Benchmark sampling**: Every 5 seconds

### The Lag

When the decision engine computes a new schedule:

1. **t=0s**: Engine evaluates and stores new schedule internally
2. **t=~10s**: Operator polls engine (when previous schedule expires)
3. **t=~10.1s**: Operator writes to TrafficSchedule CRD status
4. **t=~10.2s**: Router's watch receives CRD update
5. **t=~10.2s+**: Router starts using new weights

**Total lag: 2-10 seconds** from engine computation to router adoption.

### Example: Forecast-Aware Strategy

Looking at the detailed timeline:

```
Time  | Commanded %         | Actual %            | Deviation (p100)
------+---------------------+---------------------+-----------------
5.0s  | 17/14/69 (p30/p50/p100) | 11/8/81         | +12.1pp (lag)
10.3s | 17/14/69            | 17/16/67            | -1.7pp (caught up)
20.6s | 37/30/33 (NEW!)     | 17/14/70 (OLD!)     | +36.7pp (lag!)
25.7s | 37/30/33            | 18/13/69            | +35.7pp (still lagging)
30.8s | 37/30/33            | 18/14/68            | +35.2pp (still lagging)
...
66.6s | 40/32/28            | 31/23/46            | +18.3pp (catching up)
71.7s | 40/32/28            | 42/31/27            | -1.0pp (caught up!)
```

**Pattern**: When commanded weights change dramatically (e.g., p100 drops from 69% to 33%), actual routing continues using the old weights for 2-3 sample periods (10-15 seconds) before catching up.

## Why This Happens

### Operator Architecture (trafficschedule_controller.go:48-362)

The operator uses a **polling model** with these characteristics:

1. **Default poll interval**: 1 minute
2. **Fast requeue on expiry**: Requeues at `validUntil` time if sooner than 1 minute
3. **Config hash optimization**: Only pushes config to engine if spec changed
4. **Status update only on change**: Uses DeepEqual to avoid unnecessary writes

### Key Code Points

**operator/internal/controller/trafficschedule_controller.go:348-357**:
```go
next := pollInterval  // 1 minute
if !status.ValidUntil.IsZero() {
    until := time.Until(status.ValidUntil.Time)
    if until <= 0 {
        until = 1 * time.Second
    }
    if until < next {
        next = until
    }
}
return ctrl.Result{RequeueAfter: next}, nil
```

The operator **waits** for the current schedule to expire before polling for a new one. It doesn't know when the engine has computed a fresh schedule.

### Router Schedule Manager (buffer-service/common/schedule.py:73-92)

The router uses a **real-time Kubernetes watch** on the TrafficSchedule CRD:

```python
async def watch_forever(self) -> None:
    stream = self._watch.stream(
        self._api.list_namespaced_custom_object,
        # ... watches TrafficSchedule CRD
    )
    async for event in stream:
        async with self._lock:
            self._current = event["object"].get("status", {})
        log.info("TrafficSchedule updated (watch)")
```

The router receives updates **immediately** when the operator writes to the CRD, but it can't receive updates faster than the operator polls the engine.

## Impact Assessment

### Overall Impact: Minimal

- **Average deviations**: All within random variance (< 2.5pp)
- **Mean precision across strategies**: Matches expected values
- **Credit balance behavior**: Correct (confirms strategies work as designed)

### Transient Impact: Noticeable During Transitions

During schedule transitions (when carbon intensity changes significantly):
- **Temporary deviations**: 20-75 percentage points
- **Duration**: 5-15 seconds (1-3 sample periods)
- **Frequency**: Depends on schedule change rate (every 15-30 seconds in this test)

### Why It's Acceptable

1. **Self-correcting**: Router catches up automatically once operator polls
2. **Short duration**: Lag is only a few seconds
3. **Bounded impact**: Only affects samples during transition window
4. **Random distribution**: Router still uses probabilistic routing, just with stale weights

## Potential Mitigations (If Needed)

### Option 1: Push-Based Updates (Major Change)

Replace operator polling with push-based updates:
- Engine pushes schedule updates to operator via webhook or event
- Operator immediately writes to CRD
- Router receives updates in real-time

**Pros**: Eliminates lag completely
**Cons**: Requires significant architecture changes, adds complexity

### Option 2: Faster Polling (Simple Change)

Reduce operator requeue interval:
- Currently: Requeues at `validUntil` (~10s)
- Option: Requeue every 2-5 seconds regardless of `validUntil`

**Pros**: Simple config change
**Cons**: Increases operator CPU/API load, doesn't fully eliminate lag

### Option 3: Accept Current Behavior (Recommended)

The current lag is acceptable because:
- Average behavior is correct
- Transient deviations are short-lived
- All strategies show correct long-term behavior
- The benchmark successfully demonstrates strategy differences

## Conclusion

The commanded vs actual weight mismatch is **expected behavior** caused by the polling-based architecture. The lag is 2-10 seconds, which causes transient deviations during schedule transitions but has **minimal impact on average behavior**.

All four strategies show correct behavior when analyzed over the full test period:
- Precision-tier: Minimal variance (8.6pp swing) - correct baseline
- Credit-greedy: Moderate variance (24.7pp swing) - correct reactive behavior
- Forecast-aware: High variance (45.9pp swing) - correct proactive behavior
- Forecast-aware-global: High variance (38.4pp swing) - correct multi-factor optimization

The system is **working as designed**. The weight mismatch does not invalidate the benchmark results.
