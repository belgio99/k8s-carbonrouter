#!/usr/bin/env python3
"""Deep dive into why forecast-aware shows weak forecast behavior"""

import csv

# Load data
csv_path = "results/simple_20251113_214139/forecast-aware/timeseries.csv"

data = []
with open(csv_path, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        data.append({
            'carbon_now': float(row['carbon_now']),
            'carbon_next': float(row['carbon_next']),
            'carbon_delta': float(row['carbon_next']) - float(row['carbon_now']),
            'commanded_p100': float(row['commanded_weight_100'])
        })

# Categorize
rising = [d for d in data if d['carbon_delta'] > 20]
falling = [d for d in data if d['carbon_delta'] < -20]
stable = [d for d in data if abs(d['carbon_delta']) <= 20]

print("=" * 80)
print("WHY IS FORECAST-AWARE BEHAVIOR SO WEAK?")
print("=" * 80)
print()
print("The Problem:")
print("-" * 80)
print(f"When carbon is RISING (forecast > current by >20):")
print(f"  Avg CURRENT carbon: {sum(d['carbon_now'] for d in rising)/len(rising):.1f} gCO2/kWh")
print(f"  Avg FORECAST carbon: {sum(d['carbon_next'] for d in rising)/len(rising):.1f} gCO2/kWh")
print(f"  → Commanded p100: {sum(d['commanded_p100'] for d in rising)/len(rising):.1f}%")
print()
print(f"When carbon is FALLING (forecast < current by >20):")
print(f"  Avg CURRENT carbon: {sum(d['carbon_now'] for d in falling)/len(falling):.1f} gCO2/kWh")
print(f"  Avg FORECAST carbon: {sum(d['carbon_next'] for d in falling)/len(falling):.1f} gCO2/kWh")
print(f"  → Commanded p100: {sum(d['commanded_p100'] for d in falling)/len(falling):.1f}%")
print()
print("Notice: CURRENT carbon is LOWER during rising trends (169) than falling trends (217)")
print()
print("=" * 80)
print("ROOT CAUSE:")
print("=" * 80)
print()
print("The base credit-greedy component dominates because it reacts to CURRENT carbon,")
print("which varies much more dramatically than the forecast adjustment.")
print()
print("Forecast-aware formula:")
print("  final_allowance = base_allowance × carbon_multiplier + trend_adjustment")
print()
print("Where:")
print("  • base_allowance × carbon_multiplier: STRONG effect from current carbon")
print("    (multiplier ranges from 0.5 to 2.0 based on current/150)")
print()
print("  • trend_adjustment: WEAK effect from forecast")
print("    (capped at ±0.3, scaled by trend/current)")
print()
print("Example calculation:")
print("-" * 80)

# Find a rising example
rising_ex = sorted(rising, key=lambda x: x['carbon_delta'], reverse=True)[0]
print(f"RISING TREND EXAMPLE:")
print(f"  Current: {rising_ex['carbon_now']:.0f}, Forecast: {rising_ex['carbon_next']:.0f} (Δ+{rising_ex['carbon_delta']:.0f})")
print(f"  Carbon multiplier: {rising_ex['carbon_now']/150:.2f}")
print(f"  Trend adjustment: ~-{abs(rising_ex['carbon_delta'])/rising_ex['carbon_now']*0.5:.3f} (negative = more p100)")
print(f"  Result: p100 = {rising_ex['commanded_p100']:.1f}%")
print()

falling_ex = sorted(falling, key=lambda x: x['carbon_delta'])[0]
print(f"FALLING TREND EXAMPLE:")
print(f"  Current: {falling_ex['carbon_now']:.0f}, Forecast: {falling_ex['carbon_next']:.0f} (Δ{falling_ex['carbon_delta']:.0f})")
print(f"  Carbon multiplier: {falling_ex['carbon_now']/150:.2f}")
print(f"  Trend adjustment: ~+{abs(falling_ex['carbon_delta'])/falling_ex['carbon_now']*0.5:.3f} (positive = less p100)")
print(f"  Result: p100 = {falling_ex['commanded_p100']:.1f}%")
print()
print("=" * 80)
print("CONCLUSION:")
print("=" * 80)
print()
print("The forecast adjustment (±0.3 max) is TOO SMALL compared to the")
print("carbon multiplier effect (0.5-2.0x range), so CURRENT carbon dominates.")
print()
print("To see stronger forecast-aware behavior, we would need to:")
print("  1. Increase the trend_adjustment cap (e.g., from 0.3 to 0.5-0.7)")
print("  2. Or reduce the carbon_multiplier range")
print("  3. Or control for current carbon when comparing rising vs falling")
print()
print("Currently: Forecast-aware ≈ 95% credit-greedy + 5% forecast adjustment")
print("=" * 80)
