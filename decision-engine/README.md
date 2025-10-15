# Decision Engine

The decision engine computes carbon-aware routing schedules for each
`TrafficSchedule` custom resource. It evaluates available service strategies,
tracks a credit ledger to keep precision within budget, and publishes the
resulting weights to both the API and the CRD status consumed by the operator.

## Responsibilities

- Ingest configuration overrides from the operator via `PUT /config/<ns>/<name>`.
- Maintain a long-lived scheduler session per `(namespace, name)` pair.
- Evaluate the active scheduling policy at regular intervals and publish the
  latest schedule.
- Support temporary manual overrides (`POST /schedule/<ns>/<name>/manual`).
- Expose Prometheus metrics (credit balance, forecast data, flavour weights).

See `docs/credit_scheduler.md` for the underlying credit-based design.

## API Surface

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/schedule` | Returns the default schedule (namespace/name from env). |
| `GET` | `/schedule/<namespace>/<name>` | Returns the latest schedule for the selected workload. |
| `PUT` | `/config/<namespace>/<name>` | Applies runtime overrides (target error, bounds, strategies). |
| `POST` | `/schedule/<namespace>/<name>/manual` | Publishes a manual schedule for one TTL window. |
| `POST` | `/setschedule` | Shortcut for overriding the default schedule. |
| `GET` | `/healthz` | Readiness/liveness probe. |

Schedules follow the contract documented in `scheduler/models.py` and include
flavour weights, diagnostics, processing throttle, and credit statistics.

## Environment Variables

| Name | Default | Description |
| ---- | ------- | ----------- |
| `DEFAULT_SCHEDULE_NAMESPACE` | `default` | Namespace for the implicit `/schedule` endpoint. |
| `DEFAULT_SCHEDULE_NAME` | `default` | `TrafficSchedule` name for the implicit `/schedule` endpoint. |
| `TARGET_ERROR` | `0.05` | Allowed mean error between requested precision and realised precision. |
| `CREDIT_MIN` | `-0.5` | Lower bound for the credit ledger. |
| `CREDIT_MAX` | `0.5` | Upper bound for the credit ledger. |
| `CREDIT_WINDOW` | `300` | Smoothing window (seconds) for the credit ledger. |
| `SCHEDULER_POLICY` | `credit-greedy` | Active policy (`credit-greedy`, `forecast-aware`, `precision-tier`). |
| `SCHEDULE_VALID_FOR` | `60` | Duration (seconds) a schedule remains valid. |
| `STRATEGY_DISCOVERY_INTERVAL` | `60` | Interval between strategy refreshes. |
| `CARBON_API_TARGET` | `national` | Forecast provider scope (depends on adapter implementation). |
| `CARBON_API_TIMEOUT` | `2.0` | Timeout in seconds for carbon forecast requests. |
| `CARBON_API_CACHE_TTL` | `300.0` | Cache expiry for forecast responses. |
| `SCHEDULER_STRATEGIES` | unset | JSON array of default strategies when discovery is unavailable. |
| `METRICS_PORT` | `8001` | Prometheus exporter port. |
| `LOGLEVEL` | `INFO` | Logging verbosity. |

## Running Locally

```bash
cd decision-engine
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python decision-engine.py
```

The service listens on port 80. To validate, publish a mock configuration and
fetch the generated schedule:

```bash
curl -X PUT http://localhost/config/default/default \
  -H "Content-Type: application/json" \
  -d '{"strategies": [{"precision": 1.0}, {"precision": 0.85}, {"precision": 0.7}]}'

curl http://localhost/schedule
```

Prometheus metrics are exposed on `:METRICS_PORT` (default 8001).

## Code Structure

- `decision-engine.py` - Flask entrypoint, scheduler session registry, REST API.
- `scheduler/engine.py` - Core orchestration (policies, ledger, metrics, scaling).
- `scheduler/models.py` - Data classes shared across modules.
- `scheduler/policies.py` - Implementations of credit and forecast-aware heuristics.
- `scheduler/providers.py` - Carbon-intensity and demand forecast adapters.
- `scheduler/ledger.py` - Sliding-window credit ledger used by policies.

Unit tests live next to each module (look for `*_test.py` files) and can be run
with `pytest` once dependencies are installed.
