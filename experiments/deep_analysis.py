#!/usr/bin/env python3
"""Deep analysis of forecast-aware vs forecast-aware-global behavior."""

import pandas as pd
import numpy as np

ENERGY_P30 = 0.30
ENERGY_P50 = 0.50
ENERGY_P100 = 1.00

# Load strategies
strategies = {
    'forecast-aware': 'results/simple_20251118_184912/forecast-aware/timeseries.csv',
    'forecast-aware-global': 'results/simple_20251118_190413/forecast-aware-global/timeseries.csv',
}

def nonlinear_carbon_weight(c):
    if c <= 0:
        return 0.0
    base = 100.0
    x = c / base
    if x >= 1.0:
        return float(x ** 1.8)
    else:
        return float(x ** 0.4)

for name, path in strategies.items():
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    print(f"\n{'='*80}")
    print(f"STRATEGY: {name}")
    print(f"{'='*80}")

    # Calculate energy and carbon per slot
    df['energy'] = (
        df['requests_precision_30'] * ENERGY_P30 +
        df['requests_precision_50'] * ENERGY_P50 +
        df['requests_precision_100'] * ENERGY_P100
    )
    df['carbon_linear'] = df['carbon_now'] * df['energy']
    df['carbon_nonlinear'] = df['carbon_linear'] * df['carbon_now'].apply(nonlinear_carbon_weight)
    df['total_requests'] = (
        df['requests_precision_30'] +
        df['requests_precision_50'] +
        df['requests_precision_100']
    )

    # Analyze carbon quartiles behavior
    print("\n1. CARBON QUARTILE ANALYSIS (Non-Linear Weighted)")
    print("="*80)

    for q_label, q_low, q_high in [
        ("Q1 (BEST - Low Carbon)", 0, 0.25),
        ("Q2 (Good)", 0.25, 0.5),
        ("Q3 (Bad)", 0.5, 0.75),
        ("Q4 (WORST - High Carbon)", 0.75, 1.0)
    ]:
        mask = (df['carbon_now'] >= df['carbon_now'].quantile(q_low)) & \
               (df['carbon_now'] <= df['carbon_now'].quantile(q_high))

        # Metrics for this quartile
        avg_precision = df.loc[mask, 'mean_precision'].mean()
        total_reqs = df.loc[mask, 'total_requests'].sum()
        pct_reqs = total_reqs / df['total_requests'].sum() * 100
        carbon_linear = df.loc[mask, 'carbon_linear'].sum()
        carbon_nonlinear = df.loc[mask, 'carbon_nonlinear'].sum()
        pct_carbon_linear = carbon_linear / df['carbon_linear'].sum() * 100
        pct_carbon_nonlinear = carbon_nonlinear / df['carbon_nonlinear'].sum() * 100
        avg_carbon = df.loc[mask, 'carbon_now'].mean()

        print(f"\n{q_label} (avg carbon: {avg_carbon:.1f}):")
        print(f"  Requests:     {total_reqs:>7.0f} ({pct_reqs:>5.1f}% of total)")
        print(f"  Precision:    {avg_precision:>7.3f}")
        print(f"  Linear CO2:   {carbon_linear:>12,.0f} ({pct_carbon_linear:>5.1f}% of total)")
        print(f"  NonLinear CO2:{carbon_nonlinear:>12,.0f} ({pct_carbon_nonlinear:>5.1f}% of total)")

    # Analyze aggressiveness during low carbon
    print("\n\n2. AGGRESSIVENESS DURING LOW CARBON (bottom 25%)")
    print("="*80)
    low_carbon_mask = df['carbon_now'] <= df['carbon_now'].quantile(0.25)
    low_carbon_data = df.loc[low_carbon_mask]

    print(f"  Samples in low carbon: {len(low_carbon_data)}")
    print(f"  Max precision achieved: {low_carbon_data['mean_precision'].max():.4f}")
    print(f"  Min precision achieved: {low_carbon_data['mean_precision'].min():.4f}")
    print(f"  Avg precision: {low_carbon_data['mean_precision'].mean():.4f}")
    print(f"  Credit balance range: {low_carbon_data['credit_balance'].min():.4f} to {low_carbon_data['credit_balance'].max():.4f}")
    print(f"  Times at max credit (1.0): {(low_carbon_data['credit_balance'] >= 1.0).sum()}")
    print(f"  Avg commanded p100 weight: {low_carbon_data['commanded_weight_100'].mean():.1f}%")

    # Analyze conservativeness during high carbon
    print("\n\n3. CONSERVATIVENESS DURING HIGH CARBON (top 25%)")
    print("="*80)
    high_carbon_mask = df['carbon_now'] >= df['carbon_now'].quantile(0.75)
    high_carbon_data = df.loc[high_carbon_mask]

    print(f"  Samples in high carbon: {len(high_carbon_data)}")
    print(f"  Max precision achieved: {high_carbon_data['mean_precision'].max():.4f}")
    print(f"  Min precision achieved: {high_carbon_data['mean_precision'].min():.4f}")
    print(f"  Avg precision: {high_carbon_data['mean_precision'].mean():.4f}")
    print(f"  Credit balance range: {high_carbon_data['credit_balance'].min():.4f} to {high_carbon_data['credit_balance'].max():.4f}")
    print(f"  Times at min credit (-1.0): {(high_carbon_data['credit_balance'] <= -1.0).sum()}")
    print(f"  Avg commanded p100 weight: {high_carbon_data['commanded_weight_100'].mean():.1f}%")

    # Identify missed opportunities
    print("\n\n4. MISSED OPPORTUNITIES (Low carbon but not aggressive)")
    print("="*80)
    missed_opps = df.loc[low_carbon_mask & (df['mean_precision'] < 0.95)]
    if len(missed_opps) > 0:
        print(f"  {len(missed_opps)} slots with carbon < 25th percentile but precision < 0.95")
        print(f"  Avg precision in these slots: {missed_opps['mean_precision'].mean():.4f}")
        print(f"  Avg carbon wasted: {(missed_opps['carbon_linear'] * (0.95 - missed_opps['mean_precision'])).sum():,.0f}")
        print(f"  Example rows:")
        for idx, row in missed_opps.head(5).iterrows():
            print(f"    t={row['elapsed_seconds']:>6.1f}s carbon={row['carbon_now']:>5.1f} precision={row['mean_precision']:.3f} credit={row['credit_balance']:>6.3f}")
    else:
        print(f"  No missed opportunities - all low carbon slots used aggressively!")

    # Calculate carbon efficiency
    print("\n\n5. CARBON EFFICIENCY METRICS")
    print("="*80)
    total_carbon_linear = df['carbon_linear'].sum()
    total_carbon_nonlinear = df['carbon_nonlinear'].sum()
    total_precision = df['mean_precision'].mean()

    print(f"  Total linear CO2: {total_carbon_linear:,.0f}")
    print(f"  Total non-linear CO2: {total_carbon_nonlinear:,.0f}")
    print(f"  Avg precision: {total_precision:.4f}")
    print(f"  Linear efficiency: {total_precision / (total_carbon_linear / 1e6):.6f} precision per Mt CO2")
    print(f"  Non-linear efficiency: {total_precision / (total_carbon_nonlinear / 1e6):.6f} precision per Mt CO2")

print("\n" + "="*80)
print("SUMMARY INSIGHTS")
print("="*80)
print("\nTo make forecast-aware-global BEST, it needs to:")
print("1. Be MORE AGGRESSIVE during low carbon (Q1) - process MORE requests at HIGH precision")
print("2. Be MORE CONSERVATIVE during high carbon (Q4) - process FEWER requests at LOW precision")
print("3. Take advantage of hitting credit limit (1.0) during low carbon to maximize quality")
print("4. Use global view to identify BEST opportunities, not average them out")
