#!/usr/bin/env python3
"""Quick analysis of credit-greedy with baseline_carbon fix"""

import csv

# Read the timeseries data
csv_path = "results/simple_20251113_212832/credit-greedy/timeseries.csv"

low_carbon_p100 = []
high_carbon_p100 = []

with open(csv_path, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        carbon = float(row['carbon_now'])
        p100 = float(row['commanded_weight_100'])

        if carbon <= 80:
            low_carbon_p100.append(p100)
        elif carbon >= 240:
            high_carbon_p100.append(p100)

# Calculate averages
low_avg = sum(low_carbon_p100) / len(low_carbon_p100) if low_carbon_p100 else 0
high_avg = sum(high_carbon_p100) / len(high_carbon_p100) if high_carbon_p100 else 0

print("=" * 70)
print("CREDIT-GREEDY WITH BASELINE_CARBON = 150 (FIXED)")
print("=" * 70)
print()
print(f"Low Carbon (≤80 gCO₂/kWh): {len(low_carbon_p100)} samples")
print(f"  Average p100 weight: {low_avg:.1f}%")
print()
print(f"High Carbon (≥240 gCO₂/kWh): {len(high_carbon_p100)} samples")
print(f"  Average p100 weight: {high_avg:.1f}%")
print()
print(f"P100 Swing: {low_avg - high_avg:.1f} percentage points")
print()
if low_avg - high_avg >= 20:
    print("✅ EXCELLENT: Swing ≥20pp shows STRONG carbon-aware behavior")
elif low_avg - high_avg >= 10:
    print("✅ GOOD: Swing ≥10pp shows carbon-aware behavior")
elif low_avg - high_avg >= 5:
    print("⚠️  FAIR: Swing ≥5pp shows weak carbon-aware behavior")
else:
    print("❌ POOR: Swing <5pp shows negligible carbon-aware behavior")
print()
print("Comparison to BEFORE fix (baseline_carbon = 50):")
print("  Before: 55% (low) → 51% (high) = +4.0pp swing (FAIR)")
print(f"  After:  {low_avg:.1f}% (low) → {high_avg:.1f}% (high) = +{low_avg - high_avg:.1f}pp swing")
print(f"  Improvement: {(low_avg - high_avg) - 4.0:.1f} percentage points!")
print("=" * 70)
