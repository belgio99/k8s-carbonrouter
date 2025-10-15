# Grafana Dashboard - TrafficSchedule Status

This Grafana dashboard provides a comprehensive visualization of the data exposed in the status of TrafficSchedule resources.

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

### 4. Credit Ledger (Balance & Velocity)

* **Type**: Time Series
* **Description**: Credit balance and rate of change over time
* **Metrics**:

  * `scheduler_credit_balance`
  * `scheduler_credit_velocity`

### 5. Average Precision

* **Type**: Gauge
* **Description**: Average precision delivered by the system
* **Metric**: `scheduler_avg_precision`
* **Thresholds**: Red < 60%, Orange < 80%, Yellow < 95%, Green ≥ 95%

### 6. Processing Throttle

* **Type**: Gauge
* **Description**: Throttling factor applied to downstream processing (0–1)
* **Metric**: `scheduler_processing_throttle`

### 7. Effective Replica Ceilings

* **Type**: Time Series
* **Description**: Effective replica limits for each component
* **Metric**: `scheduler_effective_replica_ceiling`

### 8. Policy Strategy Selection Rate

* **Type**: Time Series (Bar Chart)
* **Description**: Frequency of selection for different policy strategies
* **Metric**: `rate(scheduler_policy_choice_total[5m])`

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
* **Namespace**: Kubernetes namespace where the TrafficSchedule resides
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
