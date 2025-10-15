# Grafana Dashboard - TrafficSchedule Status

This Grafana dashboard provides a comprehensive visualization of the data exposed in the status of TrafficSchedule resources.

## Template Variables

The dashboard uses the following template variables:

* **Decision Engine Namespace**: The namespace where the decision-engine is deployed (typically `carbonrouter-system`)
* **Application Namespace**: The namespace where the scheduled application is deployed (e.g., `carbonstat`)
* **Schedule**: The TrafficSchedule resource name to monitor

## Dashboard Contents

The dashboard includes the following panels:

### 1. Traffic Distribution by Flavour (Precision)

* **Type**: Time Series (Stacked Area)
* **Description**: Shows the distribution of traffic among different flavours (precision levels) over time
* **Metric**: `schedule_flavour_weight`

### 2. Carbon Intensity Now

* **Type**: Gauge
* **Description**: Current carbon intensity in gCO2/kWh
* **Metric**: `scheduler_forecast_intensity{horizon="now"}`
* **Thresholds**: Green < 100, Yellow < 200, Orange < 300, Red ≥ 300

### 3. Carbon Intensity Next

* **Type**: Gauge
* **Description**: Forecasted carbon intensity for the next slot
* **Metric**: `scheduler_forecast_intensity{horizon="next"}`

### 4. Carbon Intensity Forecast Timeline

* **Type**: Time Series (Points)
* **Description**: Extended carbon intensity forecast up to 48 hours ahead. Each point represents a forecast value, labeled with its horizon offset (e.g., "0.5h ahead", "1.0h ahead").
* **Metric**: `scheduler_forecast_intensity{horizon=~"[0-9]+\\.[0-9]+h"}`
* **Note**: The visualization uses points instead of lines to clearly show discrete forecast values. Each series represents a different time horizon (e.g., "0.5h", "1.0h", "2.5h").

### 5. Forecast Schedule (Target Times)

* **Type**: Table
* **Description**: Displays the forecast schedule in tabular format, showing the horizon offset and corresponding carbon intensity forecast
* **Metric**: `scheduler_forecast_intensity{horizon=~"[0-9]+\\.[0-9]+h"}` (instant query)
* **Columns**:
  * **Horizon**: Time offset from now (e.g., "0.5h", "1.0h")
  * **Forecast (gCO2/kWh)**: Predicted carbon intensity value with color-coded background based on thresholds
* **Note**: This table provides a clear view of when each forecast applies by showing the horizon offset. To calculate the actual target time, add the horizon to the current time shown in the "now" gauge.

### 6. Credit Ledger (Balance & Velocity)

* **Type**: Time Series
* **Description**: Credit balance and rate of change over time
* **Metrics**:

  * `scheduler_credit_balance`
  * `scheduler_credit_velocity`

### 7. Average Precision

* **Type**: Gauge
* **Description**: Average precision delivered by the system
* **Metric**: `scheduler_avg_precision`
* **Thresholds**: Red < 60%, Orange < 80%, Yellow < 95%, Green ≥ 95%

### 8. Processing Throttle

* **Type**: Gauge
* **Description**: Throttling factor applied to downstream processing (0–1)
* **Metric**: `scheduler_processing_throttle`

### 9. Effective Replica Ceilings

* **Type**: Time Series
* **Description**: Effective replica limits for each component
* **Metric**: `scheduler_effective_replica_ceiling`

### 10. Policy Strategy Selection Rate

* **Type**: Time Series (Bar Chart)
* **Description**: Frequency of selection for different policy strategies
* **Metric**: `sum by (strategy) (rate(scheduler_policy_choice_total[5m]))`

### 11. Active Policy

* **Type**: Table
* **Description**: Displays the currently active scheduling policy (e.g., "forecast-aware")
* **Metric**: Derived from `scheduler_credit_balance` labels

### 10. Router Request Rate by Flavour

* **Type**: Time Series
* **Description**: HTTP request rate handled by the router, broken down by precision flavour
* **Metric**: `sum by (flavour) (rate(router_http_requests_total[5m]))`

### 11. Consumer Message Rate by Flavour

* **Type**: Time Series
* **Description**: AMQP messages consumed per second, grouped by flavour
* **Metric**: `sum by (flavour) (rate(consumer_messages_total[5m]))`

### 12. Actual Replicas by Precision

* **Type**: Time Series
* **Description**: Current replica count from kube-state-metrics, grouped by precision label
* **Metric**: `sum by (label_carbonstat_precision) (kube_deployment_labels{namespace="$app_namespace", label_carbonstat_precision!=""} * on(namespace, deployment) group_left kube_deployment_status_replicas{namespace="$app_namespace"})`
* **Note**: Uses a join between `kube_deployment_labels` and `kube_deployment_status_replicas` because custom labels are only available in the labels metric

### 13. Router Request Latency

* **Type**: Time Series
* **Description**: Request latency percentiles (p50, p95, p99) from the router
* **Metrics**: `histogram_quantile` on `router_request_duration_seconds_bucket`

## Automatic Installation

The dashboard is automatically installed when running:

```bash
helm install carbonrouter ./helm/carbonrouter-umbrella
```

The dashboard will be automatically loaded into Grafana via the sidecar that monitors ConfigMaps labeled with `grafana_dashboard: "1"`.

## Accessing the Dashboard

1. **Port-forward Grafana**:

   ```bash
   kubectl port-forward -n carbonrouter-system svc/carbonrouter-kube-prometheus-sta-grafana 3000:80
   ```

2. **Access Grafana**:

   * URL: [http://localhost:3000](http://localhost:3000)
   * Username: `admin`
   * Password: `admin` (default, configurable in `values.yaml`)

3. **Find the Dashboard**:

   * Search for "TrafficSchedule Status" in the search bar
   * Or navigate to "Dashboards" → "Browse" → look for the "trafficschedule" tag

## Dashboard Variables

The dashboard includes three variables for filtering data:

* **Datasource**: Prometheus data source to use
* **Namespace**: Kubernetes namespace where the TrafficSchedule resides (default: `carbonstat`)
* **Schedule**: Specific TrafficSchedule resource name to monitor

These variables are automatically populated from Prometheus data and allow visualization of multiple TrafficSchedules coexisting in the cluster.

## Customization

To disable automatic dashboard installation, set the following in `values.yaml`:

```yaml
grafana:
  dashboards:
    enabled: false
```

## Technical Notes

* The dashboard refreshes automatically every 10 seconds
* Default time range: “Last hour”
* All metrics are exported by the decision-engine via the Prometheus client library
* The dashboard is compatible with Grafana 9.x and newer
* Queries use `max` and `max by` aggregations to handle multiple decision-engine instances (e.g., after restarts), always showing the most recent value
