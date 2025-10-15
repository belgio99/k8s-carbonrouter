# Credit-based Immediate Scheduling Design

## Goals

- Eliminate deferred execution: every incoming request is handled immediately.
- Keep the mean quality error within a target tolerance while minimising emitted carbon.
- Leverage deployment metadata (`carbonstat.precision`) together with carbon intensity and demand forecasts.
- Support multiple heuristics/policies to enable experimentation and apples-to-apples comparisons.
- Produce actionable metrics for routing, autoscaling, and observability.

## Core concepts

### Strategy profile

Every runnable variant of the target service is represented as a **strategy profile**:

| Field | Description |
|---|---|
| `name` | Human-readable identifier (`high-power`, `mid-power`, `team-x-low`) |
| `precision` | Estimated quality ratio ∈ (0, 1]; `1.0` == baseline/high accuracy |
| `carbon_intensity` | Estimated grams CO₂eq per req or per unit work. Defaults to the current region intensity when unknown. |
| `latency_weight` | Optional cost factor for responsiveness-sensitive workloads. |
| `enabled` | Toggle set by operator or config gates. |

Precision values come from the `carbonstat.precision` label on Kubernetes deployments.

### Credit ledger

For each logical workload (service namespace/name pair) we maintain a **credit ledger** tracking the cumulative gap between the target error budget and the realised error:

```text
Δ error(request) = target_error - (1 - precision(strategy))
credit_balanceₜ = clamp(credit_balanceₜ₋₁ + Δ error(request), credit_min, credit_max)
```

- A positive balance allows the scheduler to "spend" credit by choosing lower-precision strategies.
- A negative balance forces the scheduler to pick high-precision variants until the ledger is back within tolerance.
- Bounds avoid unbounded drift and are exported as Prometheus metrics.
- Target error is configurable per workload (default 5%).

We store a sliding window of the last N decisions (default 5 minutes) to expose running averages without requiring per-request persistence.

### Forecast adapters

The decision engine collects optional forecasts via pluggable adapters:

- **Carbon intensity forecast** – ingest from the UK Carbon Intensity API (if configured) and expose the present half-hour slot, the next slot, and the parsed schedule returned by the service.
- **Request demand forecast** – simple exponential smoothing fed by observed request rate from router metrics. Can fall back to equal weighting when unavailable.

Adapters are resilient: if a provider fails, heuristics gracefully degrade to using only current-state data.

## Heuristic families

We ship three heuristics implementing a common `SchedulerPolicy` interface:

1. **CreditGreedyPolicy** – ranks strategies by carbon savings per unit error penalty. Picks the most carbon-efficient option while the credit balance is non-negative; otherwise escalates to the highest-precision one.
2. **ForecastAwarePolicy** – similar to `CreditGreedy` but anticipates upcoming high-carbon slots. When the near-term intensity is rising, it conservatively rebuilds credit; when intensity is expected to drop, it spends credit more aggressively.
3. **PrecisionTierPolicy** – partitions strategies into high/medium/low tiers based on precision thresholds. It allocates requests proportionally to maintain the desired average precision while respecting the ledger.

Each policy yields a per-strategy probability distribution for the next scheduling window. The router converts this distribution into routing weights among the available flavours (all traffic is drained through RabbitMQ).

## Output contract

The decision engine runs one scheduler per `namespace/name` pair.

- `PUT /config/<namespace>/<name>` updates the runtime knobs for that workload (all fields are optional).
- `GET /schedule/<namespace>/<name>` surfaces the most recent decision, while `GET /schedule` aliases the default pair configured via environment variables.

Every schedule payload conforms to the following structure:

```json
{
  "flavourWeights": {"high-power": 40, "mid-power": 35, "team-low": 25},
  "flavourRules": [
    {"flavourName": "high-power", "weight": 40, "deadlineSec": 40},
    {"flavourName": "mid-power", "weight": 35, "deadlineSec": 120},
    {"flavourName": "team-low", "weight": 25, "deadlineSec": 300}
  ],
  "deadlines": {"high-power": 40, "mid-power": 120, "team-low": 300},
  "strategies": [
    {"name": "high-power", "precision": 100, "weight": 40, "deadline": 40},
    {"name": "mid-power", "precision": 85, "weight": 35, "deadline": 120},
    {"name": "team-low", "precision": 70, "weight": 25, "deadline": 300}
  ],
  "validUntil": "2025-10-09T12:34:00Z",
  "credits": {
    "balance": 0.36,
    "velocity": -0.04,
    "target": 0.1,
    "min": -1.0,
    "max": 1.0
  },
  "policy": {"name": "forecast-aware"},
  "processing": {
    "throttle": 0.72,
    "creditsRatio": 0.64,
    "intensityRatio": 0.89,
    "ceilings": {"router": 8, "consumer": 5}
  },
  "diagnostics": {
    "intensity_now": 322,
    "intensity_next": 345,
    "expected_error": 0.049
  }
}
```

Additional metadata lives under `credits` and `policy` to avoid breaking existing consumers.

## Kubernetes integration

1. **Strategy discovery** – the operator inspects deployments in the TrafficSchedule namespace labelled with `carbonstat.precision` and publishes the resulting profiles to the decision engine during every reconcile. For each matching deployment it reads:

    - `carbonstat.precision` → precision ratio (float, defaults to 1.0 when missing or invalid). Values greater than 1 are treated as percentages.
    - Optional `carbonstat.strategy` → stable name; otherwise the deployment name is used.
    - `carbonstat.deadline` → deadline in seconds (falls back to 120 when absent).

1. **Queue naming** – router and consumer derive queue names using `<service>.<namespace>.<strategy>`. Existing `high/mid/low` aliases are preserved for backwards compatibility via a lookup table.

1. **Autoscaling hints** – the decision engine exports per-strategy request rates and credit stats so that KEDA triggers can scale down aggressive strategies when credit is insufficient.

## Metrics & observability

| Metric | Labels | Description |
|---|---|---|
| `schedule_flavour_weight` | `namespace`, `schedule`, `flavour` | Instantaneous routing weights per strategy flavour. |
| `schedule_valid_until` | `namespace`, `schedule` | UNIX timestamp for the decision expiry. |
| `scheduler_credit_balance` | `namespace`, `schedule`, `policy` | Current credit ledger balance. |
| `scheduler_credit_velocity` | `namespace`, `schedule`, `policy` | Smoothed derivative of the credit balance. |
| `scheduler_avg_precision` | `namespace`, `schedule`, `policy` | Rolling average precision realised over the window. |
| `scheduler_processing_throttle` | `namespace`, `schedule`, `policy` | Downstream autoscaling throttle factor. |
| `scheduler_effective_replica_ceiling` | `namespace`, `schedule`, `component` | Throttled replica ceilings per component (`min`/`max` aware). |
| `scheduler_policy_choice_total` | `namespace`, `schedule`, `policy`, `strategy` | Counter incremented for each routing decision. |
| `scheduler_forecast_intensity` | `namespace`, `schedule`, `policy`, `horizon` | Carbon intensity forecasts in gCO₂/kWh. |

These complement existing weight gauges.

## Configuration surfaces

- Environment variables (`TARGET_ERROR`, `CREDIT_MAX`, `DEFAULT_POLICY`, `CARBON_API_URL`, `CARBON_API_TARGET`, `CARBON_API_TIMEOUT`, `CARBON_API_CACHE_TTL`, `DEFAULT_SCHEDULE_NAMESPACE`, `DEFAULT_SCHEDULE_NAME`, …).
- REST configuration via `PUT /config/<namespace>/<name>` with the same keys plus optional component autoscaling bounds (`components.<name>.minReplicas` / `maxReplicas`).
- Optional ConfigMap containing policy-specific parameters (e.g., forecast horizons, smoothing factors).
- Feature flags to enable/disable deployments without restarting the engine.

## Next steps

1. Implement the core modules (`ledger.py`, `policies.py`, `providers.py`).
2. Refactor `/schedule` endpoint to assemble outputs from the active policy.
3. Update router/consumer to honour dynamic strategy sets and deadlines.
4. Wire Prometheus metrics and unit tests.
5. Extend operator CRDs to surface precision metadata and policy selection.
