#!/usr/bin/env python3
"""
Simulate different forecast-aware parameters using existing benchmark data.
This helps predict which parameters to test without running full benchmarks.
"""

import csv
from typing import List, Dict, Tuple

def simulate_forecast_aware(data: List[Dict], cap: float, scale: float) -> Dict:
    """Simulate forecast-aware behavior with given parameters"""

    rising_samples = []
    falling_samples = []
    stable_samples = []

    for row in data:
        carbon_now = row['carbon_now']
        carbon_next = row['carbon_next']
        delta = carbon_next - carbon_now

        # Calculate base allowance (from credit-greedy)
        # This is approximated from commanded weights
        base_p100_weight = row['commanded_weight_100']

        # Calculate forecast adjustment with NEW parameters
        trend = delta
        adjustment = 0.0
        if trend > 0:
            adjustment = -min(cap, abs(trend) / max(carbon_now, 1e-6) * scale)
        elif trend < 0:
            adjustment = min(cap, abs(trend) / max(carbon_now, 1e-6) * scale)

        # Apply adjustment (simplified - assumes baseline gets opposite adjustment)
        # In reality this goes through the weight redistribution logic
        simulated_p100 = max(0, min(100, base_p100_weight - adjustment * 100))

        sample = {
            'carbon_now': carbon_now,
            'carbon_next': carbon_next,
            'delta': delta,
            'base_p100': base_p100_weight,
            'adjustment': adjustment,
            'simulated_p100': simulated_p100
        }

        if delta > 20:
            rising_samples.append(sample)
        elif delta < -20:
            falling_samples.append(sample)
        else:
            stable_samples.append(sample)

    # Calculate metrics
    rising_avg = sum(s['simulated_p100'] for s in rising_samples) / len(rising_samples) if rising_samples else 0
    falling_avg = sum(s['simulated_p100'] for s in falling_samples) / len(falling_samples) if falling_samples else 0
    stable_avg = sum(s['simulated_p100'] for s in stable_samples) / len(stable_samples) if stable_samples else 0

    forecast_swing = rising_avg - falling_avg

    # Calculate adjustment utilization
    significant_adjustments = sum(1 for s in data if abs(s.get('adjustment', 0)) > 0.1)
    adj_utilization = significant_adjustments / len(data) * 100 if data else 0

    return {
        'cap': cap,
        'scale': scale,
        'rising_avg_p100': rising_avg,
        'falling_avg_p100': falling_avg,
        'stable_avg_p100': stable_avg,
        'forecast_swing': forecast_swing,
        'rising_samples': len(rising_samples),
        'falling_samples': len(falling_samples),
        'stable_samples': len(stable_samples),
        'adj_utilization': adj_utilization
    }

def load_benchmark_data(csv_path: str) -> List[Dict]:
    """Load benchmark data from CSV"""
    data = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append({
                'carbon_now': float(row['carbon_now']),
                'carbon_next': float(row['carbon_next']),
                'commanded_weight_100': float(row['commanded_weight_100'])
            })
    return data

def main():
    # Load existing data (we'll use this to simulate)
    csv_path = "results/simple_20251113_214139/forecast-aware/timeseries.csv"
    data = load_benchmark_data(csv_path)

    print("=" * 80)
    print("FORECAST PARAMETER SIMULATOR")
    print("=" * 80)
    print()
    print(f"Using data from: {csv_path}")
    print(f"Loaded {len(data)} samples")
    print()

    # Parameter combinations to test
    caps = [0.3, 0.5, 0.6, 0.8, 1.0]
    scales = [0.5, 0.8, 1.0, 1.2, 1.5]

    results = []
    for cap in caps:
        for scale in scales:
            result = simulate_forecast_aware(data, cap, scale)
            results.append(result)

    # Sort by forecast swing
    results.sort(key=lambda x: x['forecast_swing'], reverse=True)

    print("=" * 80)
    print("TOP 10 PARAMETER COMBINATIONS (by forecast-aware swing)")
    print("=" * 80)
    print()
    print(f"{'Rank':<6} {'Cap':<6} {'Scale':<7} {'Forecast Swing':<15} {'Rising p100':<12} {'Falling p100':<13} {'Status'}")
    print("-" * 80)

    for i, r in enumerate(results[:10], 1):
        status = ""
        if r['forecast_swing'] >= 15:
            status = "STRONG ✅"
        elif r['forecast_swing'] >= 10:
            status = "MODERATE ✓"
        elif r['forecast_swing'] >= 5:
            status = "FAIR ⚠️"
        else:
            status = "WEAK ❌"

        print(f"{i:<6} {r['cap']:<6.1f} {r['scale']:<7.1f} {r['forecast_swing']:+.1f}pp{'':<10} "
              f"{r['rising_avg_p100']:.1f}%{'':<6} {r['falling_avg_p100']:.1f}%{'':<7} {status}")

    print()
    print("=" * 80)
    print("CURRENT BASELINE (cap=0.3, scale=0.5)")
    print("=" * 80)
    baseline = [r for r in results if r['cap'] == 0.3 and r['scale'] == 0.5][0]
    print(f"  Forecast swing: {baseline['forecast_swing']:+.1f}pp")
    print(f"  Rising p100: {baseline['rising_avg_p100']:.1f}%")
    print(f"  Falling p100: {baseline['falling_avg_p100']:.1f}%")
    print()

    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    # Find moderate and strong candidates
    moderate = [r for r in results if 10 <= r['forecast_swing'] < 15][0] if any(10 <= r['forecast_swing'] < 15 for r in results) else None
    strong = [r for r in results if r['forecast_swing'] >= 15][0] if any(r['forecast_swing'] >= 15 for r in results) else None

    if moderate:
        print(f"RECOMMENDED START (Moderate impact):")
        print(f"  cap = {moderate['cap']:.1f}, scale = {moderate['scale']:.1f}")
        print(f"  Expected forecast swing: {moderate['forecast_swing']:+.1f}pp")
        print(f"  Improvement over baseline: {moderate['forecast_swing'] - baseline['forecast_swing']:+.1f}pp")
        print()

    if strong:
        print(f"AGGRESSIVE OPTION (Strong impact):")
        print(f"  cap = {strong['cap']:.1f}, scale = {strong['scale']:.1f}")
        print(f"  Expected forecast swing: {strong['forecast_swing']:+.1f}pp")
        print(f"  Improvement over baseline: {strong['forecast_swing'] - baseline['forecast_swing']:+.1f}pp")
        print(f"  ⚠️  Monitor stability - may cause oscillations")
        print()

    print("=" * 80)
    print("IMPLEMENTATION")
    print("=" * 80)
    print()
    print("To test a configuration:")
    print("1. Edit: /decision-engine/scheduler/strategies/forecast_aware.py")
    print("2. Lines 32 & 34, change:")
    print(f"     adjustment = -min({moderate['cap'] if moderate else 0.6}, trend / max(forecast.intensity_now, 1e-6) * {moderate['scale'] if moderate else 1.0})")
    print("3. Commit & push, wait 45s, delete decision-engine pod")
    print("4. Run: python3 run_simple_benchmark.py --policy forecast-aware")
    print("5. Analyze with forecast_aware_analysis.ipynb")
    print()
    print("NOTE: These are simulated estimates. Actual results may vary due to:")
    print("  - Credit balance dynamics")
    print("  - Traffic queueing effects")
    print("  - Weight redistribution logic")
    print("=" * 80)

if __name__ == "__main__":
    main()
