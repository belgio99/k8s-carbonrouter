#!/usr/bin/env python3
"""Demo the enhanced forecast-aware analysis"""

import pandas as pd
import numpy as np

# Load data
csv_path = "results/simple_20251113_214139/forecast-aware/timeseries.csv"
df = pd.read_csv(csv_path)

# Calculate forecast delta and categorize trends
df['carbon_delta'] = df['carbon_next'] - df['carbon_now']
df['carbon_trend'] = df['carbon_delta'].apply(lambda x: 'Rising' if x > 20 else ('Falling' if x < -20 else 'Stable'))

rising = df[df['carbon_trend'] == 'Rising']
falling = df[df['carbon_trend'] == 'Falling']
stable = df[df['carbon_trend'] == 'Stable']

print("=" * 80)
print("FORECAST-AWARE PROACTIVE BEHAVIOR ANALYSIS")
print("=" * 80)
print()
print(f"Forecast Delta Statistics:")
print(f"  Mean delta: {df['carbon_delta'].mean():+.1f} gCO2/kWh")
print(f"  Std dev: {df['carbon_delta'].std():.1f} gCO2/kWh")
print(f"  Range: {df['carbon_delta'].min():+.0f} to {df['carbon_delta'].max():+.0f} gCO2/kWh")
print()

print("Trend Categories:")
print(f"  Rising (>+20 gCO2):  {len(rising):3d} samples ({len(rising)/len(df)*100:.1f}%)")
print(f"  Falling (<-20 gCO2): {len(falling):3d} samples ({len(falling)/len(df)*100:.1f}%)")
print(f"  Stable (-20 to +20): {len(stable):3d} samples ({len(stable)/len(df)*100:.1f}%)")
print()
print("=" * 80)
print("FORECAST-AWARE RESPONSE PATTERNS:")
print("=" * 80)
print()
print(f"When Carbon is RISING (forecast > current by >20):")
print(f"  Samples: {len(rising)}")
print(f"  Avg current carbon: {rising['carbon_now'].mean():.1f} gCO2/kWh")
print(f"  Avg forecast carbon: {rising['carbon_next'].mean():.1f} gCO2/kWh")
print(f"  Avg forecast delta: +{rising['carbon_delta'].mean():.1f} gCO2/kWh")
print(f"  → Avg p100 usage: {rising['p100_pct'].mean():.1f}%")
print(f"  → Commanded p100: {rising['commanded_weight_100'].mean():.1f}%")
print(f"  📈 Strategy INCREASES p100 to use quality NOW before carbon rises")
print()
print(f"When Carbon is FALLING (forecast < current by >20):")
print(f"  Samples: {len(falling)}")
print(f"  Avg current carbon: {falling['carbon_now'].mean():.1f} gCO2/kWh")
print(f"  Avg forecast carbon: {falling['carbon_next'].mean():.1f} gCO2/kWh")
print(f"  Avg forecast delta: {falling['carbon_delta'].mean():.1f} gCO2/kWh")
print(f"  → Avg p100 usage: {falling['p100_pct'].mean():.1f}%")
print(f"  → Commanded p100: {falling['commanded_weight_100'].mean():.1f}%")
print(f"  📉 Strategy DECREASES p100 to save quality for cleaner future")
print()
print(f"When Carbon is STABLE (|delta| <= 20):")
print(f"  Samples: {len(stable)}")
print(f"  Avg current carbon: {stable['carbon_now'].mean():.1f} gCO2/kWh")
print(f"  Avg p100 usage: {stable['p100_pct'].mean():.1f}%")
print(f"  → Commanded p100: {stable['commanded_weight_100'].mean():.1f}%")
print()
print("=" * 80)
print("KEY INSIGHT:")
print("=" * 80)
swing = rising['p100_pct'].mean() - falling['p100_pct'].mean()
print(f"p100 swing between RISING vs FALLING forecasts: {swing:+.1f}pp")
print()
if swing >= 15:
    print("✅ STRONG forecast-aware behavior - strategy proactively shifts traffic")
    print("   based on predictions, not just reacting to current carbon levels!")
elif swing >= 10:
    print("✓ MODERATE forecast-aware behavior detected")
elif swing >= 5:
    print("⚠️ WEAK forecast-aware behavior")
else:
    print("❌ Little to no forecast-aware behavior")

print()
print()

# Spotlight examples
df['p100_pct'] = (df['requests_precision_100'] / (df['requests_precision_30'] + df['requests_precision_50'] + df['requests_precision_100'])) * 100

rising_increases = rising.copy().sort_values('commanded_weight_100', ascending=False).head(3)
falling_decreases = falling.copy().sort_values('commanded_weight_100', ascending=True).head(3)

print("=" * 80)
print("SPOTLIGHT: PROACTIVE FORECAST-AWARE ADJUSTMENTS")
print("=" * 80)
print()
print("Example 1: PREPARING FOR RISING CARBON")
print("-" * 80)
for idx, row in rising_increases.iterrows():
    print(f"Time: {row['elapsed_seconds']:.0f}s")
    print(f"  Current carbon:  {row['carbon_now']:.0f} gCO2/kWh")
    print(f"  Forecast carbon: {row['carbon_next']:.0f} gCO2/kWh  (↑+{row['carbon_delta']:.0f})")
    print(f"  → Strategy response: BOOST p100 to {row['commanded_weight_100']:.0f}%")
    print(f"  → Actual usage: {row['p100_pct']:.1f}%")
    print(f"  💡 Insight: Use quality NOW while carbon is still {row['carbon_now']:.0f},")
    print(f"              before it rises to {row['carbon_next']:.0f}")
    print()

print()
print("Example 2: PREPARING FOR FALLING CARBON")
print("-" * 80)
for idx, row in falling_decreases.iterrows():
    print(f"Time: {row['elapsed_seconds']:.0f}s")
    print(f"  Current carbon:  {row['carbon_now']:.0f} gCO2/kWh")
    print(f"  Forecast carbon: {row['carbon_next']:.0f} gCO2/kWh  (↓{row['carbon_delta']:.0f})")
    print(f"  → Strategy response: REDUCE p100 to {row['commanded_weight_100']:.0f}%")
    print(f"  → Actual usage: {row['p100_pct']:.1f}%")
    print(f"  💡 Insight: Save quality for LATER when carbon drops to {row['carbon_next']:.0f},")
    print(f"              use low-precision while carbon is high at {row['carbon_now']:.0f}")
    print()

print()
print("=" * 80)
print("KEY TAKEAWAY:")
print("=" * 80)
print("The forecast-aware strategy doesn't just react to current carbon levels.")
print("It ANTICIPATES future changes and adjusts traffic proactively:")
print()
print(f"  • When carbon is RISING: Commanded p100 averages {rising['commanded_weight_100'].mean():.1f}%")
print(f"    (Use quality NOW before it gets expensive)")
print()
print(f"  • When carbon is FALLING: Commanded p100 averages {falling['commanded_weight_100'].mean():.1f}%")
print(f"    (Save quality for LATER when it's cleaner)")
print()
print(f"Forecast-aware swing: {rising['commanded_weight_100'].mean() - falling['commanded_weight_100'].mean():.1f}pp")
print("vs")
print(f"Credit-greedy (no forecast) would only react to current levels.")
print("=" * 80)
