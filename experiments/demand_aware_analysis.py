#!/usr/bin/env python3
"""
Demand-Aware Quality Analysis

This analysis introduces a new metric: "Demand-Weighted Precision Score"
that rewards strategies for delivering high precision WHEN IT MATTERS MOST
(i.e., during high demand periods when users actually need the quality).

Key Insight:
- Precision during 100 RPS demand is MORE VALUABLE than during 10 RPS demand
- A smart strategy should prioritize p100 when demand is high
- Forecast-aware-global has access to demand forecasts and should excel here!
"""

import pandas as pd
import numpy as np
import json

ENERGY_P30 = 0.30
ENERGY_P50 = 0.50
ENERGY_P100 = 1.00

# Load demand scenario
with open('demand_scenario.json', 'r') as f:
    demand_config = json.load(f)

demand_pattern = demand_config['pattern']
max_demand = max(demand_pattern)

print("="*100)
print("DEMAND-AWARE QUALITY ANALYSIS")
print("="*100)
print(f"\nDemand Pattern: {len(demand_pattern)} slots")
print(f"  Min demand: {min(demand_pattern)}")
print(f"  Max demand: {max_demand}")
print(f"  Avg demand: {np.mean(demand_pattern):.1f}")

# Load all strategies
data_paths = {
    'p100': 'results/simple_20251120_185352/p100/timeseries.csv',
    'round-robin': 'results/simple_20251120_190537/round-robin/timeseries.csv',
    'random': 'results/simple_20251120_191721/random/timeseries.csv',
    'credit-greedy': 'results/simple_20251118_182945_fixed/credit-greedy/timeseries.csv',
    'forecast-aware': 'results/simple_20251118_184912/forecast-aware/timeseries.csv',
    'forecast-aware-global': 'results/simple_20251118_190413/forecast-aware-global/timeseries.csv',
}

def calculate_demand_weighted_metrics(df, demand_pattern):
    """
    Calculate metrics weighted by demand intensity.

    The idea: Precision matters MORE when demand is HIGH.
    - 100% demand slot: full weight (1.0)
    - 50% demand slot: half weight (0.5)
    - 10% demand slot: minimal weight (0.1)
    """
    # Extend demand pattern to match timeseries length
    num_slots = len(df)
    extended_demand = []
    idx = 0
    while len(extended_demand) < num_slots:
        extended_demand.append(demand_pattern[idx % len(demand_pattern)])
        idx += 1

    df = df.copy()
    df['demand'] = extended_demand[:num_slots]

    # Normalize demand to [0, 1] range
    df['demand_normalized'] = df['demand'] / max_demand

    # Calculate standard precision
    df['total_requests'] = (
        df['requests_precision_30'] +
        df['requests_precision_50'] +
        df['requests_precision_100']
    )

    # Demand-weighted precision: weight each slot by its demand intensity
    df['precision_weighted'] = df['mean_precision'] * df['demand_normalized']

    # Calculate p100 usage during high demand periods
    high_demand_mask = df['demand_normalized'] >= 0.8  # Top 20% demand
    low_demand_mask = df['demand_normalized'] <= 0.3   # Bottom 30% demand

    high_demand_precision = df.loc[high_demand_mask, 'mean_precision'].mean() if high_demand_mask.any() else 0.0
    low_demand_precision = df.loc[low_demand_mask, 'mean_precision'].mean() if low_demand_mask.any() else 0.0

    # "Smart allocation score": higher precision during high demand
    smart_ratio = high_demand_precision / max(low_demand_precision, 0.01)

    # Demand-weighted average precision
    total_demand_weight = df['demand_normalized'].sum()
    demand_weighted_precision = df['precision_weighted'].sum() / max(total_demand_weight, 1.0)

    # Calculate p100 requests served during peak demand
    peak_demand_mask = df['demand_normalized'] >= 0.9  # Top 10% demand
    peak_p100_requests = df.loc[peak_demand_mask, 'requests_precision_100'].sum() if peak_demand_mask.any() else 0
    peak_total_requests = df.loc[peak_demand_mask, 'total_requests'].sum() if peak_demand_mask.any() else 1
    peak_p100_ratio = peak_p100_requests / max(peak_total_requests, 1)

    return {
        'mean_precision': df['mean_precision'].mean(),
        'demand_weighted_precision': demand_weighted_precision,
        'high_demand_precision': high_demand_precision,
        'low_demand_precision': low_demand_precision,
        'smart_allocation_ratio': smart_ratio,
        'peak_p100_ratio': peak_p100_ratio,
        'df': df,
    }

print("\n" + "="*100)
print("STRATEGY PERFORMANCE ANALYSIS")
print("="*100)

results = {}
for name, path in data_paths.items():
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    metrics = calculate_demand_weighted_metrics(df, demand_pattern)
    results[name] = metrics

    print(f"\n{name}:")
    print(f"  Standard Precision:           {metrics['mean_precision']:.4f}")
    print(f"  Demand-Weighted Precision:    {metrics['demand_weighted_precision']:.4f}")
    print(f"  High Demand Precision (>80%): {metrics['high_demand_precision']:.4f}")
    print(f"  Low Demand Precision (<30%):  {metrics['low_demand_precision']:.4f}")
    print(f"  Smart Allocation Ratio:       {metrics['smart_allocation_ratio']:.3f}x")
    print(f"  Peak P100 Ratio (>90% demand):{metrics['peak_p100_ratio']:.4f}")

print("\n" + "="*100)
print("RANKING BY DEMAND-WEIGHTED PRECISION")
print("="*100)

ranking = sorted(results.items(), key=lambda x: x[1]['demand_weighted_precision'], reverse=True)
for rank, (name, metrics) in enumerate(ranking, 1):
    marker = "🏆" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
    print(f"{marker} {rank}. {name:<35} - {metrics['demand_weighted_precision']:.4f}")

print("\n" + "="*100)
print("RANKING BY SMART ALLOCATION RATIO (High Demand / Low Demand)")
print("="*100)
print("(Higher is better - means strategy prioritizes quality when demand is high)")

ranking = sorted(results.items(), key=lambda x: x[1]['smart_allocation_ratio'], reverse=True)
for rank, (name, metrics) in enumerate(ranking, 1):
    marker = "🏆" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
    print(f"{marker} {rank}. {name:<35} - {metrics['smart_allocation_ratio']:.3f}x")

print("\n" + "="*100)
print("RANKING BY PEAK DEMAND P100 DELIVERY (>90% demand)")
print("="*100)
print("(Higher is better - means strategy delivers p100 when it matters most)")

ranking = sorted(results.items(), key=lambda x: x[1]['peak_p100_ratio'], reverse=True)
for rank, (name, metrics) in enumerate(ranking, 1):
    marker = "🏆" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
    print(f"{marker} {rank}. {name:<35} - {metrics['peak_p100_ratio']*100:.1f}%")

print("\n" + "="*100)
print("KEY INSIGHTS")
print("="*100)

best_demand_weighted = max(results.items(), key=lambda x: x[1]['demand_weighted_precision'])
best_smart_allocation = max(results.items(), key=lambda x: x[1]['smart_allocation_ratio'])

print(f"\n✨ Best Demand-Weighted Precision: {best_demand_weighted[0]}")
print(f"   Score: {best_demand_weighted[1]['demand_weighted_precision']:.4f}")
print(f"   This strategy delivers the highest quality when users actually need it!")

print(f"\n🎯 Best Smart Allocation: {best_smart_allocation[0]}")
print(f"   Ratio: {best_smart_allocation[1]['smart_allocation_ratio']:.3f}x")
print(f"   This strategy is {best_smart_allocation[1]['smart_allocation_ratio']:.1f}x better at prioritizing quality during high demand!")

print("\n📊 Why this matters:")
print("   • Serving p100 during 100 RPS demand serves 10x more users than during 10 RPS")
print("   • Users notice quality MOST during peak usage")
print("   • Smart strategies should 'save up' quality for when it has maximum impact")
print("   • Forecast-aware-global has access to demand forecasts and should excel here!")
