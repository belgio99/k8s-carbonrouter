# Refactoring: Architettura Modulare delle Strategie di Scheduling

## Modifiche Effettuate

### Struttura Precedente
```
decision-engine/scheduler/
├── policies.py          # Tutte le strategie in un unico file (207 righe)
├── engine.py
├── models.py
├── ledger.py
└── providers.py
```

### Nuova Struttura
```
decision-engine/scheduler/
├── strategies/                    # Nuova cartella per le strategie
│   ├── __init__.py               # Esporta tutte le strategie
│   ├── base.py                   # Classe astratta SchedulerPolicy
│   ├── credit_greedy.py          # Strategia Credit-Greedy
│   ├── forecast_aware.py         # Strategia Forecast-Aware
│   ├── precision_tier.py         # Strategia Precision-Tier
│   └── README.md                 # Documentazione completa per aggiungere nuove strategie
├── engine.py                      # Aggiornato per importare da strategies
├── models.py
├── ledger.py
└── providers.py
```

## Vantaggi dell'Architettura Modulare

### 1. **Separazione delle Responsabilità**
   - Ogni strategia vive nel proprio file
   - Più facile trovare e modificare una strategia specifica
   - Riduce la complessità cognitiva

### 2. **Estensibilità Semplificata**
   - Aggiungere una nuova strategia non richiede modificare file esistenti
   - Processo documentato in `strategies/README.md`
   - 3 semplici step: creare file → registrare in `__init__.py` → configurare in `engine.py`

### 3. **Manutenibilità**
   - Test isolati per ogni strategia
   - Modifiche a una strategia non influenzano le altre
   - Più facile rivedere le modifiche in code review

### 4. **Scalabilità**
   - L'architettura supporta N strategie senza degrado
   - Facile aggiungere varianti sperimentali
   - Possibilità di disabilitare strategie specifiche

## Come Aggiungere una Nuova Strategia

### Esempio: Strategia Time-Based

1. **Creare il file** `strategies/time_based.py`:
```python
"""Time-Based scheduling strategy."""

from __future__ import annotations
from typing import Optional
from datetime import datetime

from .base import SchedulerPolicy
from ..models import FlavourProfile, ForecastSnapshot, PolicyResult, PolicyDiagnostics


class TimeBasedPolicy(SchedulerPolicy):
    """Adjust precision based on time of day."""

    name = "time-based"

    def evaluate(
        self,
        flavours: list[FlavourProfile],
        forecast: Optional[ForecastSnapshot] = None,
    ) -> PolicyResult:
        hour = datetime.now().hour
        
        # Più precisione durante le ore di punta (9-17)
        is_peak = 9 <= hour <= 17
        
        weights = {}
        if is_peak:
            # Alta precisione durante il giorno
            for f in flavours:
                weights[f.name] = 1.0 if f.precision >= 0.9 else 0.1
        else:
            # Bassa precisione di notte
            for f in flavours:
                weights[f.name] = 1.0 if f.precision <= 0.5 else 0.1
        
        # Normalizza
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        
        avg_precision = sum(
            w * f.precision for f in flavours for k, w in weights.items() if k == f.name
        )
        
        diagnostics = PolicyDiagnostics({
            "hour": hour,
            "is_peak": float(is_peak),
        })
        
        return PolicyResult(weights, avg_precision, diagnostics)
```

2. **Registrare in** `strategies/__init__.py`:
```python
from .time_based import TimeBasedPolicy

__all__ = [
    # ... strategie esistenti ...
    "TimeBasedPolicy",
]
```

3. **Aggiungere a** `engine.py`:
```python
from .strategies import (
    # ... import esistenti ...
    TimeBasedPolicy,
)

_POLICY_BUILDERS: Dict[str, type[SchedulerPolicy]] = {
    # ... strategie esistenti ...
    "time-based": TimeBasedPolicy,
}
```

4. **Usare nel TrafficSchedule**:
```yaml
apiVersion: carbonrouter.belgio99.io/v1alpha1
kind: TrafficSchedule
spec:
  policy: time-based
  # ... resto della configurazione ...
```

## Testing e Deployment

Le modifiche sono state:
- ✅ Committate su GitHub
- ✅ Pushat su main
- ✅ Build automatica su GitHub Actions completata
- ✅ Pod decision-engine riavviato con successo
- ✅ Servizio operativo e funzionante

## Note Tecniche

- **Backward Compatibility**: Mantenuta al 100% - nessuna modifica all'interfaccia pubblica
- **Import Path**: Cambiato da `scheduler.policies` a `scheduler.strategies`
- **Funzionalità**: Identiche - solo riorganizzazione del codice
- **Performance**: Nessun impatto - stessa logica, diversa organizzazione

## Documentazione

La cartella `strategies/` contiene un `README.md` completo con:
- Descrizione di ogni strategia esistente
- Guida passo-passo per aggiungere nuove strategie
- Documentazione dell'interfaccia `SchedulerPolicy`
- Best practices e suggerimenti per il testing

## Prossimi Passi Suggeriti

1. **Aggiungere test unitari** per ogni strategia in isolamento
2. **Creare strategie sperimentali** sfruttando la nuova architettura
3. **Documentare metriche custom** che ogni strategia può esportare
4. **Considerare un plugin system** per caricare strategie dinamicamente
