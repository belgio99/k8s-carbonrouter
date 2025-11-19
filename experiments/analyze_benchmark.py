#!/usr/bin/env python3
"""
Analyze benchmark timeseries data for forecast-aware-global strategy.

Compares rebalanced weights (new) with previous run (old weights).
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# Load the benchmark runs to compare
NEW_RUN = "simple_20251119_180013/forecast-aware-global"  # forecast-aware-global
OLD_RUN = "simple_20251119_190241/credit-greedy"  # credit-greedy

def load_data(run_path):
    """Load timeseries data from benchmark run."""
    full_path = Path("results") / run_path / "timeseries.csv"
    if not full_path.exists():
        print(f"❌ File not found: {full_path}")
        return None

    df = pd.read_csv(full_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def analyze_run(df, label):
    """Analyze a single benchmark run."""
    print(f"\n{'='*80}")
    print(f"{label} ANALYSIS")
    print(f"{'='*80}\n")

    # Basic statistics
    print(f"Duration: {df['elapsed_seconds'].max():.1f} seconds")
    print(f"Total requests: {df['delta_requests'].sum():,}")
    print(f"Avg requests/sec: {df['delta_requests'].sum() / df['elapsed_seconds'].max():.1f}")
    print()

    # Precision statistics
    print("PRECISION:")
    print(f"  Mean delivered:    {df['mean_precision'].mean():.4f}")
    print(f"  Std deviation:     {df['mean_precision'].std():.4f}")
    print(f"  Min:               {df['mean_precision'].min():.4f}")
    print(f"  Max:               {df['mean_precision'].max():.4f}")
    print(f"  Target (engine):   {df['engine_avg_precision'].mean():.4f}")
    print()

    # Credit balance
    print("CREDIT BALANCE:")
    print(f"  Final balance:     {df['credit_balance'].iloc[-1]:.4f}")
    print(f"  Min balance:       {df['credit_balance'].min():.4f}")
    print(f"  Max balance:       {df['credit_balance'].max():.4f}")
    print(f"  Avg velocity:      {df['credit_velocity'].mean():.6f}")
    print()

    # Carbon exposure
    print("CARBON EXPOSURE:")
    print(f"  Avg carbon_now:    {df['carbon_now'].mean():.1f} gCO2/kWh")
    print(f"  Min carbon:        {df['carbon_now'].min():.1f} gCO2/kWh")
    print(f"  Max carbon:        {df['carbon_now'].max():.1f} gCO2/kWh")
    print()

    # Flavour usage (approximate carbon footprint)
    # Assuming p30=0.3 carbon, p50=0.6 carbon, p100=1.0 carbon (relative)
    total_req = df['delta_requests'].sum()
    p30_reqs = df['requests_precision_30'].sum()
    p50_reqs = df['requests_precision_50'].sum()
    p100_reqs = df['requests_precision_100'].sum()

    print("FLAVOUR USAGE:")
    print(f"  p30 requests:      {p30_reqs:,} ({p30_reqs/total_req*100:.1f}%)")
    print(f"  p50 requests:      {p50_reqs:,} ({p50_reqs/total_req*100:.1f}%)")
    print(f"  p100 requests:     {p100_reqs:,} ({p100_reqs/total_req*100:.1f}%)")
    print()

    # Estimate relative carbon footprint (normalized)
    carbon_footprint = (p30_reqs * 0.3 + p50_reqs * 0.6 + p100_reqs * 1.0) / total_req
    print(f"  Relative carbon footprint: {carbon_footprint:.4f}")
    print(f"  (0.3 = all p30, 1.0 = all p100)")
    print()

    # Weight distribution over time
    print("COMMANDED WEIGHTS (average):")
    print(f"  p30:  {df['commanded_weight_30'].mean():.1f}%")
    print(f"  p50:  {df['commanded_weight_50'].mean():.1f}%")
    print(f"  p100: {df['commanded_weight_100'].mean():.1f}%")
    print()

    return {
        'total_requests': total_req,
        'mean_precision': df['mean_precision'].mean(),
        'precision_std': df['mean_precision'].std(),
        'final_credit': df['credit_balance'].iloc[-1],
        'carbon_footprint': carbon_footprint,
        'p30_pct': p30_reqs/total_req*100,
        'p50_pct': p50_reqs/total_req*100,
        'p100_pct': p100_reqs/total_req*100,
    }

def compare_runs(new_df, old_df):
    """Compare two benchmark runs."""
    print(f"\n{'='*80}")
    print("COMPARISON: forecast-aware-global vs credit-greedy")
    print(f"{'='*80}\n")

    new_stats = analyze_run(new_df, "FORECAST-AWARE-GLOBAL")
    old_stats = analyze_run(old_df, "CREDIT-GREEDY")

    print(f"\n{'='*80}")
    print("DELTA ANALYSIS")
    print(f"{'='*80}\n")

    # Calculate differences
    precision_diff = new_stats['mean_precision'] - old_stats['mean_precision']
    carbon_diff = new_stats['carbon_footprint'] - old_stats['carbon_footprint']

    print(f"Mean Precision:    {old_stats['mean_precision']:.4f} → {new_stats['mean_precision']:.4f} "
          f"({precision_diff:+.4f}, {precision_diff/old_stats['mean_precision']*100:+.2f}%)")
    print(f"Carbon Footprint:  {old_stats['carbon_footprint']:.4f} → {new_stats['carbon_footprint']:.4f} "
          f"({carbon_diff:+.4f}, {carbon_diff/old_stats['carbon_footprint']*100:+.2f}%)")
    print(f"Final Credit:      {old_stats['final_credit']:.4f} → {new_stats['final_credit']:.4f}")
    print()

    print("Flavour Usage Changes:")
    print(f"  p30:  {old_stats['p30_pct']:.1f}% → {new_stats['p30_pct']:.1f}% "
          f"({new_stats['p30_pct']-old_stats['p30_pct']:+.1f}pp)")
    print(f"  p50:  {old_stats['p50_pct']:.1f}% → {new_stats['p50_pct']:.1f}% "
          f"({new_stats['p50_pct']-old_stats['p50_pct']:+.1f}pp)")
    print(f"  p100: {old_stats['p100_pct']:.1f}% → {new_stats['p100_pct']:.1f}% "
          f"({new_stats['p100_pct']-old_stats['p100_pct']:+.1f}pp)")
    print()

    # Interpretation
    print("INTERPRETATION:")
    if carbon_diff < 0:
        print(f"  ✅ Carbon footprint reduced by {abs(carbon_diff/old_stats['carbon_footprint']*100):.1f}%")
    else:
        print(f"  ❌ Carbon footprint increased by {carbon_diff/old_stats['carbon_footprint']*100:.1f}%")

    if abs(precision_diff) < 0.01:
        print(f"  ✅ Precision maintained within 1% ({abs(precision_diff/old_stats['mean_precision']*100):.2f}%)")
    elif precision_diff > 0:
        print(f"  ✅ Precision improved by {precision_diff/old_stats['mean_precision']*100:.1f}%")
    else:
        print(f"  ⚠️  Precision decreased by {abs(precision_diff/old_stats['mean_precision']*100):.1f}%")

    print()

def create_comparison_plot(new_df, old_df):
    """Create comparison plots."""
    fig, axes = plt.subplots(3, 2, figsize=(15, 12))
    fig.suptitle('Benchmark Comparison: forecast-aware-global vs credit-greedy', fontsize=16)

    # Plot 1: Precision over time
    ax = axes[0, 0]
    ax.plot(new_df['elapsed_seconds'], new_df['mean_precision'],
            label='forecast-aware-global', linewidth=2, alpha=0.8)
    ax.plot(old_df['elapsed_seconds'], old_df['mean_precision'],
            label='credit-greedy', linewidth=2, alpha=0.8, linestyle='--')
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Mean Precision')
    ax.set_title('Delivered Precision Over Time')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 2: Credit balance
    ax = axes[0, 1]
    ax.plot(new_df['elapsed_seconds'], new_df['credit_balance'],
            label='forecast-aware-global', linewidth=2, alpha=0.8)
    ax.plot(old_df['elapsed_seconds'], old_df['credit_balance'],
            label='credit-greedy', linewidth=2, alpha=0.8, linestyle='--')
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Credit Balance')
    ax.set_title('Credit Balance Over Time')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color='r', linestyle=':', alpha=0.5)

    # Plot 3: Carbon exposure
    ax = axes[1, 0]
    ax.plot(new_df['elapsed_seconds'], new_df['carbon_now'],
            label='forecast-aware-global', linewidth=2, alpha=0.8)
    ax.plot(old_df['elapsed_seconds'], old_df['carbon_now'],
            label='credit-greedy', linewidth=2, alpha=0.8, linestyle='--')
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Carbon Intensity (gCO2/kWh)')
    ax.set_title('Grid Carbon Intensity')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 4: Flavour distribution (forecast-aware-global)
    ax = axes[1, 1]
    ax.stackplot(new_df['elapsed_seconds'],
                 new_df['commanded_weight_30'],
                 new_df['commanded_weight_50'],
                 new_df['commanded_weight_100'],
                 labels=['p30', 'p50', 'p100'],
                 alpha=0.7)
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Weight (%)')
    ax.set_title('Flavour Weights Over Time (forecast-aware-global)')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)

    # Plot 5: Request distribution (forecast-aware-global)
    ax = axes[2, 0]
    window = 5  # Rolling average window
    ax.plot(new_df['elapsed_seconds'],
            new_df['requests_precision_30'].rolling(window).mean(),
            label='p30', linewidth=2, alpha=0.7)
    ax.plot(new_df['elapsed_seconds'],
            new_df['requests_precision_50'].rolling(window).mean(),
            label='p50', linewidth=2, alpha=0.7)
    ax.plot(new_df['elapsed_seconds'],
            new_df['requests_precision_100'].rolling(window).mean(),
            label='p100', linewidth=2, alpha=0.7)
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Requests per Sample')
    ax.set_title('Request Distribution by Flavour (forecast-aware-global, 5-sample avg)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 6: Precision vs Carbon trade-off
    ax = axes[2, 1]
    ax.scatter(new_df['mean_precision'], new_df['carbon_now'],
               label='forecast-aware-global', alpha=0.6, s=30)
    ax.scatter(old_df['mean_precision'], old_df['carbon_now'],
               label='credit-greedy', alpha=0.6, s=30)
    ax.set_xlabel('Mean Precision')
    ax.set_ylabel('Carbon Intensity (gCO2/kWh)')
    ax.set_title('Precision vs Carbon Trade-off')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    # Save plot
    output_path = Path("results") / NEW_RUN / "comparison_analysis.png"
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"📊 Comparison plot saved to: {output_path}")

    return fig

if __name__ == "__main__":
    print("Loading benchmark data...")

    new_df = load_data(NEW_RUN)
    old_df = load_data(OLD_RUN)

    if new_df is None:
        print("❌ Could not load NEW run data")
        exit(1)

    if old_df is None:
        print("⚠️  Could not load OLD run data, analyzing NEW run only")
        analyze_run(new_df, "NEW RUN (Rebalanced Weights)")
    else:
        compare_runs(new_df, old_df)
        print("\nCreating comparison plots...")
        create_comparison_plot(new_df, old_df)
        print("✅ Analysis complete!")
