# Buffer Service

The buffer service bridges HTTP clients, RabbitMQ, and the target workloads.
It provides two long-running processes:

- `router.py` - FastAPI application that accepts inbound HTTP traffic, fetches
the current `TrafficSchedule`, and publishes each request to a RabbitMQ
headers exchange using the appropriate flavour headers.
- `consumer.py` - Async workers that subscribe to the per-flavour queues,
forward the embedded HTTP requests to the target service, and publish RPC-style
responses back to RabbitMQ.

Both services share the `common/` utilities (schedule cache and helpers) and
export Prometheus metrics for observability.

## Request Flow

```text
client --> router.py --> RabbitMQ exchange --> queue.<flavour>
                                     ^                  |
                                     |                  v
                                     +-------------- consumer.py
```

1. The router receives the HTTP request, chooses a flavour based on the
   `TrafficSchedule`, adds tracing headers, and publishes it to the
   `<namespace>.<service>` headers exchange.
2. The consumer keeps long-lived workers per flavour. For each message it
   performs the HTTP request against the selected target service, decorates the
   response, caches metrics, and answers on the RPC reply queue.
3. Both components expose `/metrics` for Prometheus scraping; the router also
   maintains a gauge describing how close the schedule is to expiry.

## Environment Variables

| Name | Default | Component | Description |
| ---- | ------- | --------- | ----------- |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | router, consumer | AMQP connection string. |
| `TS_NAME` | `traffic-schedule` | router, consumer | Name of the `TrafficSchedule` CRD to follow. |
| `TARGET_SVC_NAME` | `unknown-svc` | router, consumer | Kubernetes service name (lowercase). |
| `TARGET_SVC_NAMESPACE` | `default` | router, consumer | Kubernetes namespace for the target service. |
| `TARGET_SVC_SCHEME` | `http` | consumer | Scheme used when calling the target service. |
| `TARGET_SVC_PORT` | unset | consumer | Optional port override for target service requests. |
| `RPC_TIMEOUT_SEC` | `60` | router | Timeout while waiting for the RPC reply. |
| `METRICS_PORT` | `8001` | router, consumer | Port where the Prometheus exporter listens. |
| `CONCURRENCY_PER_QUEUE` | `32` | consumer | Max concurrent in-flight requests per flavour. |
| `DEBUG` | `false` | router, consumer | Enables verbose debug logging when `true`. |

## Running Locally

1. Create a Python virtual environment and install dependencies:

   ```bash
   cd buffer-service
   python -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

2. Ensure RabbitMQ is available (for example via Docker: `docker run -p 5672:5672 rabbitmq:3-management`).

3. Export the required environment variables (at minimum `RABBITMQ_URL`,
   `TARGET_SVC_NAME`, and `TARGET_SVC_NAMESPACE`).

4. Start the router and consumer in separate shells:

   ```bash
   python router.py
   python consumer.py
   ```

   The router script boots `uvicorn.Server` internally (port 8000 by default),
   while the consumer spawns its worker pools and metrics endpoint.

## Docker Images

- `Dockerfile.router` builds the router service (`uvicorn` entrypoint).
- `Dockerfile.consumer` builds the consumer service.

Use the standard workflow:

```bash
docker build -t carbonrouter-router -f Dockerfile.router .
docker build -t carbonrouter-consumer -f Dockerfile.consumer .
```

## Metrics

Key metrics exported (labels vary per flavour and queue):

- Router: `router_ingress_http_requests_total`,
  `router_request_duration_seconds`, `router_schedule_valid_seconds`,
  `router_messages_published_total`.
- Consumer: `router_http_requests_total`, `consumer_messages_total`,
  `consumer_forward_seconds`.

Scrape the router on `:METRICS_PORT/metrics` (served by Prometheus client) and
the consumer at the same path.

## Development Notes

- The `TrafficScheduleManager` in `common/schedule.py` connects to the Kubernetes
  API (in-cluster or via local kubeconfig) to watch the schedule CRD.
- Both services rely on `uvloop` for better asyncio performance; ensure it is
  available on the target platform.
- When running outside Kubernetes you can mock schedules by editing
  `DEFAULT_SCHEDULE` in `common/utils.py`.
