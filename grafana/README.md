# Grafana Dashboard - TrafficSchedule Status

Questa dashboard Grafana fornisce una visualizzazione completa dei dati esposti nello status delle risorse TrafficSchedule.

## Contenuto della Dashboard

La dashboard include i seguenti pannelli:

### 1. Traffic Distribution by Flavour (Precision)
- **Tipo**: Time Series (Stacked Area)
- **Descrizione**: Mostra la distribuzione del traffico tra i diversi flavour (livelli di precisione) nel tempo
- **Metrica**: `schedule_flavour_weight`

### 2. Carbon Intensity Now
- **Tipo**: Gauge
- **Descrizione**: Intensità di carbonio corrente in gCO2/kWh
- **Metrica**: `scheduler_forecast_intensity{horizon="now"}`
- **Soglie**: Verde < 100, Giallo < 200, Arancione < 300, Rosso ≥ 300

### 3. Carbon Intensity Next
- **Tipo**: Gauge
- **Descrizione**: Previsione dell'intensità di carbonio per il prossimo slot
- **Metrica**: `scheduler_forecast_intensity{horizon="next"}`

### 4. Credit Ledger (Balance & Velocity)
- **Tipo**: Time Series
- **Descrizione**: Bilancio dei crediti e velocità di variazione nel tempo
- **Metriche**: 
  - `scheduler_credit_balance`
  - `scheduler_credit_velocity`

### 5. Average Precision
- **Tipo**: Gauge
- **Descrizione**: Precisione media erogata dal sistema
- **Metrica**: `scheduler_avg_precision`
- **Soglie**: Rosso < 60%, Arancione < 80%, Giallo < 95%, Verde ≥ 95%

### 6. Processing Throttle
- **Tipo**: Gauge
- **Descrizione**: Fattore di throttling applicato al processing downstream (0-1)
- **Metrica**: `scheduler_processing_throttle`

### 7. Effective Replica Ceilings
- **Tipo**: Time Series
- **Descrizione**: Limiti effettivi di replica per ogni componente
- **Metrica**: `scheduler_effective_replica_ceiling`

### 8. Policy Strategy Selection Rate
- **Tipo**: Time Series (Bar Chart)
- **Descrizione**: Frequenza di selezione delle diverse strategie di policy
- **Metrica**: `rate(scheduler_policy_choice_total[5m])`

## Installazione Automatica

La dashboard viene installata automaticamente quando si esegue:

```bash
helm install carbonrouter ./helm/carbonrouter-umbrella
```

La dashboard sarà automaticamente caricata in Grafana grazie al sidecar che monitora le ConfigMap con il label `grafana_dashboard: "1"`.

## Accesso alla Dashboard

1. **Port-forward di Grafana**:
   ```bash
   kubectl port-forward -n carbonrouter-system svc/carbonrouter-kube-prometheus-sta-grafana 3000:80
   ```

2. **Accedere a Grafana**:
   - URL: http://localhost:3000
   - Username: `admin`
   - Password: `admin` (default, configurabile in `values.yaml`)

3. **Trovare la Dashboard**:
   - Cerca "TrafficSchedule Status" nella barra di ricerca
   - Oppure naviga in "Dashboards" → "Browse" → cerca il tag "trafficschedule"

## Variabili della Dashboard

La dashboard include tre variabili per filtrare i dati:

- **Datasource**: Sorgente dati Prometheus da utilizzare
- **Namespace**: Namespace Kubernetes dove risiede il TrafficSchedule
- **Schedule**: Nome specifico della risorsa TrafficSchedule da monitorare

Queste variabili vengono popolate automaticamente dai dati disponibili in Prometheus e permettono di visualizzare dati da diversi TrafficSchedule contemporaneamente presenti nel cluster.

## Personalizzazione

Per disabilitare l'installazione automatica della dashboard, impostare in `values.yaml`:

```yaml
grafana:
  dashboards:
    enabled: false
```

## Note Tecniche

- La dashboard si aggiorna automaticamente ogni 10 secondi
- Il periodo temporale di default è "Ultima ora"
- Tutte le metriche sono esportate dal decision-engine tramite Prometheus client library
- La dashboard è compatibile con Grafana 9.x e superiori
