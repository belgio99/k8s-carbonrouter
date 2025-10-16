# Forecast-Aware-Global Strategy - Sommario Tecnico

## Panoramica

La strategia **`forecast-aware-global`** implementa un'euristica avanzata di scheduling carbon-aware che considera simultaneamente tutti i segnali disponibili per ottimizzare globalmente le decisioni di scheduling.

## Fattori Considerati ✅

### 1. Credito/Debito in Termini di Errore Medio
- **Implementazione**: Utilizza `CreditLedger` per tracciare il balance cumulativo
- **Range**: `[-1.0, +1.0]`
- **Calcolo**: `Δ = target_error - (1 - precision)`
- **Comportamento**:
  - Balance positivo → credito accumulato → può usare flavour più verdi (bassa precisione)
  - Balance negativo → debito accumulato → deve usare flavour più precisi (alta precisione)

### 2. Greenness dello Slot Corrente
- **Implementazione**: `forecast.intensity_now` (gCO2eq/kWh)
- **Fonte**: UK Carbon Intensity API in tempo reale
- **Utilizzo**: Calcolo del `carbon_score` per ogni flavour
- **Formula**: `score = (carbon_baseline - carbon_flavour) / expected_error`

### 3. Previsioni Emissioni Prossimi Slot
- **Short-term**: `intensity_now` vs `intensity_next` (30 minuti)
- **Extended**: Analisi di 6 forecast points (1.5-3 ore)
- **Implementazione**: 
  - `_compute_carbon_trend_adjustment()`: trend a breve termine
  - `_compute_extended_lookahead_adjustment()`: trend esteso
- **Logica**:
  - Intensità in aumento >20%: `adjustment = -0.8` (conserva fortemente)
  - Intensità in diminuzione >20%: `adjustment = +0.8` (spendi fortemente)

### 4. Emissioni Complessive Finora ✅ **NUOVO**
- **Implementazione**: Tracker cumulativo `_cumulative_carbon`
- **Metriche**:
  - `cumulative_carbon_gco2`: totale CO2 emesso (grammi)
  - `request_count`: numero di richieste processate
  - `avg_carbon_per_request`: media gCO2/richiesta
- **Logica**:
  - Emissioni >20% sopra il rate corrente: preferisci flavour verdi
  - Emissioni <20% sotto il rate corrente: puoi usare alta precisione
  - On track: nessun aggiustamento

### 5. Previsioni di Richieste Prossimi Slot ✅ **NUOVO**
- **Implementazione**: `forecast.demand_now` vs `forecast.demand_next`
- **Fonte**: Exponential smoothing su metriche osservate
- **Logica**:
  - Spike atteso >50%: `adjustment = -0.6` (conserva per lo spike)
  - Drop atteso >30%: `adjustment = +0.4` (puoi spendere)
  - Domanda stabile: nessun aggiustamento

## Algoritmo Multi-Fattore

### Formula di Combinazione

```
total_adjustment = 
    0.35 × carbon_adjustment      +  # Trend intensità carbonica
    0.25 × demand_adjustment      +  # Previsione carico
    0.25 × emissions_adjustment   +  # Budget emissioni cumulative
    0.15 × lookahead_adjustment      # Forecast esteso

Vincolo: total_adjustment ∈ [-0.5, +0.5]
```

### Semantica degli Aggiustamenti

- **Positivo** (+): Sposta traffico verso flavour **più verdi** (bassa precisione)
- **Negativo** (−): Sposta traffico verso flavour **baseline** (alta precisione)

### Applicazione ai Pesi

1. Ordina flavour per precisione (decrescente)
2. Identifica baseline (massima precisione)
3. Se `adjustment > 0`: riduci peso baseline, incrementa altri proporzionalmente
4. Se `adjustment < 0`: incrementa peso baseline, riduci altri proporzionalmente
5. Normalizza: `Σ weights = 1.0`

## Architettura del Codice

```python
class ForecastAwareGlobalPolicy(CreditGreedyPolicy):
    """
    Eredita da CreditGreedyPolicy per riutilizzare:
    - carbon_score()
    - precision_of_name()
    - Base allocation logic
    """
    
    def __init__(self):
        self._cumulative_carbon = 0.0  # Tracker emissioni
        self._request_count = 0        # Contatore richieste
    
    def evaluate(flavours, forecast):
        # 1. Base allocation (credit-greedy)
        base = super().evaluate(flavours, forecast)
        
        # 2. Compute adjustments
        carbon_adj = _compute_carbon_trend_adjustment(forecast)
        demand_adj = _compute_demand_adjustment(forecast)
        emissions_adj = _compute_emissions_budget_adjustment(forecast)
        lookahead_adj = _compute_extended_lookahead_adjustment(forecast)
        
        # 3. Combine with weights
        total_adj = 0.35*carbon + 0.25*demand + 0.25*emissions + 0.15*lookahead
        
        # 4. Apply to weights
        weights = _apply_adjustment(base.weights, total_adj, flavours)
        
        # 5. Return with diagnostics
        return PolicyResult(weights, avg_precision, diagnostics)
```

## Metriche Esportate

### Prometheus

```promql
scheduler_credit_balance{policy="forecast-aware-global"}
scheduler_credit_velocity{policy="forecast-aware-global"}
scheduler_avg_precision{policy="forecast-aware-global"}
scheduler_forecast_intensity_timestamped{policy="forecast-aware-global"}
```

### Diagnostics JSON

```json
{
  "diagnostics": {
    "credit_balance": 0.36,
    "allowance": 0.68,
    "avg_precision": 0.923,
    "carbon_adjustment": -0.234,
    "demand_adjustment": -0.156,
    "emissions_adjustment": 0.089,
    "lookahead_adjustment": 0.045,
    "total_adjustment": -0.187,
    "cumulative_carbon_gco2": 1456.78,
    "request_count": 3421.0,
    "avg_carbon_per_request": 0.426
  }
}
```

## Confronto con Altre Strategie

| Caratteristica | credit-greedy | forecast-aware | precision-tier | **forecast-aware-global** |
|----------------|---------------|----------------|----------------|---------------------------|
| **Credit tracking** | ✅ | ✅ | ✅ | ✅ |
| **Carbon intensity now** | ✅ | ✅ | ✅ | ✅ |
| **Carbon forecast (1 step)** | ❌ | ✅ | ❌ | ✅ |
| **Extended forecast (6 steps)** | ❌ | ❌ | ❌ | ✅ |
| **Demand forecast** | ❌ | ❌ | ❌ | ✅ |
| **Cumulative emissions** | ❌ | ❌ | ❌ | ✅ |
| **Multi-factor scoring** | ❌ | ❌ | ❌ | ✅ |
| **Complessità computazionale** | O(n) | O(n) | O(n) | O(n) |
| **Overhead decisione** | ~1ms | ~2ms | ~1ms | ~5-10ms |

## Casi d'Uso Ideali

### ✅ Raccomandato Per:
1. **Ambienti di produzione** con target rigorosi di riduzione carbonio
2. **Workload variabili** con cicli diurni o traffico bursty
3. **Servizi ML/AI** con multipli modelli di precisione
4. **Ricerca e ottimizzazione** per confrontare diverse euristiche
5. **Ambienti regolamentati** che richiedono accounting delle emissioni

### ⚠️ Non Raccomandato Per:
1. **Bassa latenza ultra-critica** (<10ms) dove l'overhead conta
2. **Workload semplici** con carico costante
3. **Ambienti dev/test** dove strategie base sono sufficienti

## Scenari di Esempio

### Scenario 1: Picco di Carbonio Previsto
```
Situazione:
- intensity_now: 150 gCO2/kWh
- intensity_next: 220 gCO2/kWh (↑47%)
- demand: stabile
- credit_balance: 0.3

Calcoli:
- carbon_adjustment: -0.8 (conserva fortemente)
- demand_adjustment: 0.0 (stabile)
- emissions_adjustment: 0.0 (on track)
- lookahead_adjustment: 0.0 (non disponibile)
- total_adjustment: 0.35 × (-0.8) = -0.28

Risultato:
→ Shift verso flavour baseline (alta precisione)
→ Accumula credito per periodo pulito futuro
```

### Scenario 2: Periodo Pulito + Spike di Carico
```
Situazione:
- intensity_now: 100 gCO2/kWh
- intensity_next: 80 gCO2/kWh (↓20%)
- demand_now: 100 req/s
- demand_next: 180 req/s (↑80%)
- credit_balance: 0.5

Calcoli:
- carbon_adjustment: +0.8 (spendi, è pulito)
- demand_adjustment: -0.6 (conserva per spike)
- emissions_adjustment: 0.0
- lookahead_adjustment: 0.0
- total_adjustment: 0.35×0.8 + 0.25×(-0.6) = +0.13

Risultato:
→ Leggermente verso flavour verdi
→ Bilanciato tra opportunità carbonica e spike futuro
```

### Scenario 3: Fuori Budget Emissioni
```
Situazione:
- cumulative_carbon: 5400 gCO2
- request_count: 3000
- avg: 1.8 gCO2/req
- intensity_now: 1.2 gCO2/kWh (50% sopra)
- future 3h: molto pulito (60-80 gCO2/kWh)

Calcoli:
- carbon_adjustment: 0.0 (stabile)
- demand_adjustment: 0.0 (stabile)
- emissions_adjustment: +0.5 (serve più verde)
- lookahead_adjustment: -0.5 (conserva per periodo pulito)
- total_adjustment: 0.25×0.5 + 0.15×(-0.5) = +0.05

Risultato:
→ Leggermente più verde
→ Decisione bilanciata
```

## File e Struttura

```
decision-engine/scheduler/strategies/
├── forecast_aware_global.py    # Implementazione strategia
├── __init__.py                 # Registrazione nel modulo
└── README.md                   # Documentazione strategie

decision-engine/scheduler/
├── engine.py                   # Registry: _POLICY_BUILDERS
├── ledger.py                   # CreditLedger
├── models.py                   # ForecastSnapshot, PolicyResult
└── providers.py                # ForecastManager

docs/
├── forecast_aware_global_strategy.md  # Documentazione completa
└── credit_scheduler.md                # Design credit system

demo/
├── forecast-aware-global-example.yaml    # Configurazione esempio
└── TESTING_FORECAST_AWARE_GLOBAL.md      # Guida testing
```

## Configurazione

### Via TrafficSchedule CRD

```yaml
spec:
  config:
    policy: "forecast-aware-global"
    targetError: 0.1
    creditMax: 1.0
    creditMin: -1.0
```

### Via Environment Variables

```bash
SCHEDULER_POLICY=forecast-aware-global
TARGET_ERROR=0.1
CREDIT_MAX=1.0
CREDIT_MIN=-1.0
```

## Prossimi Passi per Miglioramento

1. **Pesi Adattivi**: Apprendere pesi ottimali 0.35/0.25/0.25/0.15 da dati storici
2. **Orizzonte Esteso**: Ottimizzare su finestre 6-24h invece di 1.5-3h
3. **Multi-Regione**: Considerare intensità carbonica attraverso datacenter
4. **Funzione di Costo**: Aggiungere costo economico all'obiettivo
5. **Machine Learning**: Predire distribuzioni ottimali con modelli trained

## Riferimenti

- Implementazione: [`decision-engine/scheduler/strategies/forecast_aware_global.py`](../decision-engine/scheduler/strategies/forecast_aware_global.py)
- Documentazione: [`docs/forecast_aware_global_strategy.md`](forecast_aware_global_strategy.md)
- Testing: [`demo/TESTING_FORECAST_AWARE_GLOBAL.md`](../demo/TESTING_FORECAST_AWARE_GLOBAL.md)
- Design: [`docs/credit_scheduler.md`](credit_scheduler.md)
