#!/usr/bin/env python3
"""
Script per eseguire test validazione carbon-aware scheduler
Registra i risultati per il Chapter 5 della tesi
"""

import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

DECISION_ENGINE_URL = "http://localhost:8080"
NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"

def run_command(cmd: str) -> str:
    """Esegue un comando shell e restituisce l'output"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def get_valid_until() -> str:
    """Calcola timestamp validUntil (10 minuti nel futuro)"""
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    return future.strftime("%Y-%m-%dT%H:%M:%SZ")

def set_manual_schedule(carbon_now: float, carbon_next: float) -> bool:
    """Imposta uno schedule manuale con valori di carbon intensity specifici"""
    valid_until = get_valid_until()
    payload = {
        "carbonForecastNow": carbon_now,
        "carbonForecastNext": carbon_next,
        "validUntil": valid_until
    }
    
    cmd = f"""curl -s -X POST {DECISION_ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}/manual \\
      -H "Content-Type: application/json" \\
      -d '{json.dumps(payload)}'"""
    
    result = run_command(cmd)
    return "schedule set" in result

def get_schedule() -> Dict[str, Any]:
    """Recupera lo schedule corrente"""
    cmd = f"curl -s {DECISION_ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}"
    result = run_command(cmd)
    try:
        return json.loads(result)
    except:
        return {}

def get_prometheus_metrics() -> Dict[str, float]:
    """Recupera metriche da Prometheus"""
    metrics = {}
    
    queries = {
        "credit_balance": "scheduler_credit_balance",
        "credit_velocity": "scheduler_credit_velocity",
        "avg_precision": "scheduler_avg_precision",
        "throttle": "scheduler_processing_throttle",
    }
    
    for name, query in queries.items():
        cmd = f"curl -s 'http://localhost:9090/api/v1/query?query={query}' | jq -r '.data.result[0].value[1]' 2>/dev/null || echo '0'"
        value = run_command(cmd)
        try:
            metrics[name] = float(value)
        except:
            metrics[name] = 0.0
    
    return metrics

def run_test(test_name: str, carbon_now: float, carbon_next: float, description: str) -> Dict[str, Any]:
    """Esegue un singolo test e raccoglie i risultati"""
    print(f"\n{'='*60}")
    print(f"Test: {test_name}")
    print(f"Description: {description}")
    print(f"Carbon: {carbon_now} → {carbon_next} gCO2/kWh")
    print(f"{'='*60}\n")
    
    # Imposta schedule manuale
    print("Setting manual schedule...")
    if not set_manual_schedule(carbon_now, carbon_next):
        print("ERROR: Failed to set manual schedule")
        return {}
    
    # Attendi che il sistema elabori
    print("Waiting for system to process (5 seconds)...")
    time.sleep(5)
    
    # Raccogli risultati
    schedule = get_schedule()
    metrics = get_prometheus_metrics()
    
    result = {
        "test_name": test_name,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "carbon_intensity": {
            "now": carbon_now,
            "next": carbon_next,
            "trend": "rising" if carbon_next > carbon_now else "falling" if carbon_next < carbon_now else "stable"
        },
        "schedule": schedule,
        "prometheus_metrics": metrics
    }
    
    # Mostra risultati
    print(f"\nResults:")
    print(f"  Carbon trend: {result['carbon_intensity']['trend']}")
    if 'diagnostics' in schedule:
        diag = schedule['diagnostics']
        print(f"  Carbon adjustment: {diag.get('carbon_adjustment', 'N/A')}")
        print(f"  Total adjustment: {diag.get('total_adjustment', 'N/A')}")
    print(f"  Credit balance: {metrics.get('credit_balance', 'N/A')}")
    print(f"  Throttle: {metrics.get('throttle', 'N/A')}")
    
    if 'flavours' in schedule:
        print(f"\n  Flavour Distribution:")
        for flavour in schedule['flavours']:
            name = flavour.get('name', 'unknown')
            weight = flavour.get('weight', 0) * 100
            print(f"    {name}: {weight:.1f}%")
    
    return result

def main():
    print("="*60)
    print("CARBON-AWARE SCHEDULER VALIDATION TESTS")
    print("="*60)
    
    tests = [
        {
            "name": "Test 1 - Rising Intensity (Morning)",
            "carbon_now": 120,
            "carbon_next": 280,
            "description": "Simula mattino con intensità carbonio in aumento. Il sistema dovrebbe conservare crediti (carbon_adjustment negativo) per prepararsi al picco."
        },
        {
            "name": "Test 2 - Falling Intensity (Evening)",
            "carbon_now": 280,
            "carbon_next": 120,
            "description": "Simula sera con intensità carbonio in diminuzione. Il sistema dovrebbe spendere crediti (carbon_adjustment positivo) favorendo precision più basse."
        },
        {
            "name": "Test 3 - Peak Intensity (Midday)",
            "carbon_now": 350,
            "carbon_next": 340,
            "description": "Simula picco giornaliero con alta intensità sostenuta. Il sistema dovrebbe adottare strategia conservativa con alta precision baseline."
        },
        {
            "name": "Test 4 - Clean Period (Night)",
            "carbon_now": 40,
            "carbon_next": 35,
            "description": "Simula periodo notturno con bassa intensità. Il sistema dovrebbe adottare strategia aggressiva green con precision più basse."
        }
    ]
    
    results = []
    for test in tests:
        result = run_test(
            test["name"],
            test["carbon_now"],
            test["carbon_next"],
            test["description"]
        )
        results.append(result)
        
        # Pausa tra i test
        if test != tests[-1]:
            print(f"\n{'='*60}")
            print("Waiting 10 seconds before next test...")
            print(f"{'='*60}")
            time.sleep(10)
    
    # Salva risultati
    output_file = "test_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"All tests completed!")
    print(f"Results saved to: {output_file}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
