#!/usr/bin/env python3
"""Analyze credit-greedy benchmark results to diagnose reverse behavior."""

import csv
import sys
from collections import defaultdict

def main():
    # Read the timeseries data
    csv_path = "results/simple_20251113_204309/credit-greedy/timeseries.csv"

    rows = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Calculate percentages
            total = float(row['requests_precision_30']) + float(row['requests_precision_50']) + float(row['requests_precision_100'])
            if total > 0:
                row['p100_pct'] = (float(row['requests_precision_100']) / total) * 100
                row['p50_pct'] = (float(row['requests_precision_50']) / total) * 100
                row['p30_pct'] = (float(row['requests_precision_30']) / total) * 100
                row['total_requests'] = total
                row['carbon_now'] = float(row['carbon_now'])
                row['credit_balance'] = float(row['credit_balance'])
                row['commanded_weight_100'] = float(row['commanded_weight_100'])
                rows.append(row)

    # Carbon level categories
    low_carbon = [r for r in rows if r['carbon_now'] <= 80]
    mid_carbon = [r for r in rows if 80 < r['carbon_now'] < 240]
    high_carbon = [r for r in rows if r['carbon_now'] >= 240]

    print("=" * 80)
    print("CREDIT-GREEDY REVERSE BEHAVIOR ANALYSIS")
    print("=" * 80)
    print()

    print("1. CARBON LEVEL BREAKDOWN:")
    print("-" * 80)
    print(f"LOW CARBON (≤80 gCO2/kWh):  {len(low_carbon):3d} samples  |  p100: {low_carbon['p100_pct'].mean():5.1f}%")
    print(f"MID CARBON (80-240):        {len(mid_carbon):3d} samples  |  p100: {mid_carbon['p100_pct'].mean():5.1f}%")
    print(f"HIGH CARBON (≥240 gCO2/kWh): {len(high_carbon):3d} samples  |  p100: {high_carbon['p100_pct'].mean():5.1f}%")
    print()
    print(f"P100 SWING (Low - High): {low_carbon['p100_pct'].mean() - high_carbon['p100_pct'].mean():+.1f} percentage points")
    print()

    # Analyze the allowance and credit balance behavior
    print("2. ALLOWANCE & CREDIT BALANCE ANALYSIS:")
    print("-" * 80)
    print(f"LOW CARBON:  Credit Balance: {low_carbon['credit_balance'].mean():+.4f}  |  Weight p100: {low_carbon['commanded_weight_100'].mean():.1f}%")
    print(f"MID CARBON:  Credit Balance: {mid_carbon['credit_balance'].mean():+.4f}  |  Weight p100: {mid_carbon['commanded_weight_100'].mean():.1f}%")
    print(f"HIGH CARBON: Credit Balance: {high_carbon['credit_balance'].mean():+.4f}  |  Weight p100: {high_carbon['commanded_weight_100'].mean():.1f}%")
    print()

    # Look at extreme carbon periods
    very_low = df[df['carbon_now'] <= 60]
    very_high = df[df['carbon_now'] >= 280]

    print("3. EXTREME CARBON PERIODS:")
    print("-" * 80)
    print(f"VERY LOW (≤60):  {len(very_low):3d} samples  |  p100: {very_low['p100_pct'].mean():5.1f}%  |  Weight: {very_low['commanded_weight_100'].mean():.1f}%  |  Credit: {very_low['credit_balance'].mean():+.4f}")
    print(f"VERY HIGH (≥280): {len(very_high):3d} samples  |  p100: {very_high['p100_pct'].mean():5.1f}%  |  Weight: {very_high['commanded_weight_100'].mean():.1f}%  |  Credit: {very_high['credit_balance'].mean():+.4f}")
    print()

    # Show actual commanded weights
    print("4. COMMANDED WEIGHT DISTRIBUTION:")
    print("-" * 80)
    unique_weights = df['commanded_weight_100'].unique()
    for weight in sorted(unique_weights):
        samples = df[df['commanded_weight_100'] == weight]
        print(f"Weight p100={weight:2.0f}%: {len(samples):3d} samples  |  Avg carbon: {samples['carbon_now'].mean():5.1f}  |  Avg p100 usage: {samples['p100_pct'].mean():5.1f}%  |  Avg credit: {samples['credit_balance'].mean():+.4f}")
    print()

    # Check if weights are actually changing properly
    print("5. CREDIT BALANCE TRAJECTORY:")
    print("-" * 80)
    print(f"Start credit balance: {df['credit_balance'].iloc[0]:+.4f}")
    print(f"End credit balance: {df['credit_balance'].iloc[-1]:+.4f}")
    print(f"Min credit balance: {df['credit_balance'].min():+.4f}")
    print(f"Max credit balance: {df['credit_balance'].max():+.4f}")
    print()

    # Show the relationship between carbon and commanded weight
    print("6. CARBON vs COMMANDED WEIGHT CORRELATION:")
    print("-" * 80)
    correlation = df[['carbon_now', 'commanded_weight_100', 'credit_balance']].corr()
    print("Correlation matrix:")
    print(correlation)
    print()

    # Look at specific transition points
    print("7. DETAILED CARBON TRANSITION ANALYSIS:")
    print("-" * 80)
    # Find transitions from low to high carbon
    df['carbon_diff'] = df['carbon_now'].diff()
    big_increases = df[df['carbon_diff'] > 100]
    big_decreases = df[df['carbon_diff'] < -100]

    print(f"Big carbon INCREASES (>100): {len(big_increases)} transitions")
    if len(big_increases) > 0:
        print("  Before -> After:")
        for idx in big_increases.index[:3]:
            if idx > 0:
                before = df.loc[idx-1]
                after = df.loc[idx]
                print(f"    Carbon: {before['carbon_now']:.0f} -> {after['carbon_now']:.0f}  |  Weight p100: {before['commanded_weight_100']:.0f}% -> {after['commanded_weight_100']:.0f}%  |  Credit: {before['credit_balance']:+.4f} -> {after['credit_balance']:+.4f}")

    print()
    print(f"Big carbon DECREASES (<-100): {len(big_decreases)} transitions")
    if len(big_decreases) > 0:
        print("  Before -> After:")
        for idx in big_decreases.index[:3]:
            if idx > 0:
                before = df.loc[idx-1]
                after = df.loc[idx]
                print(f"    Carbon: {before['carbon_now']:.0f} -> {after['carbon_now']:.0f}  |  Weight p100: {before['commanded_weight_100']:.0f}% -> {after['commanded_weight_100']:.0f}%  |  Credit: {before['credit_balance']:+.4f} -> {after['credit_balance']:+.4f}")

    print()
    print("=" * 80)
    print("DIAGNOSIS:")
    print("=" * 80)

    # Check if the issue is with commanded weights or actual usage
    low_weight_samples = df[df['commanded_weight_100'] <= 50]
    high_weight_samples = df[df['commanded_weight_100'] > 50]

    if len(low_weight_samples) > 0 and len(high_weight_samples) > 0:
        print(f"When commanded weight p100 ≤50%: Avg carbon = {low_weight_samples['carbon_now'].mean():.1f}")
        print(f"When commanded weight p100 >50%: Avg carbon = {high_weight_samples['carbon_now'].mean():.1f}")
        print()

        if low_weight_samples['carbon_now'].mean() > high_weight_samples['carbon_now'].mean():
            print("❌ PROBLEM IDENTIFIED: Lower p100 weights are commanded during HIGH carbon")
            print("   This is BACKWARDS! The algorithm should command LOW weights during HIGH carbon.")
            print("   But it's doing the OPPOSITE.")
        else:
            print("✓ Commanded weights appear correct (low weights during high carbon)")
            print("  The issue may be in how weights are translated to actual traffic distribution")

    print()
    print("Credit balance stays mostly constant around 0.5, suggesting the algorithm")
    print("is not properly responding to carbon intensity changes.")
    print()

if __name__ == "__main__":
    main()
