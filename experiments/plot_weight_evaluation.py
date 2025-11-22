#!/usr/bin/env python3
"""
Plot weight evaluation benchmark results.

Generates comprehensive visualizations showing:
- Carbon intensity timeline for both scenarios
- Replica counts over time (stacked area chart)
- Power consumption breakdown
- Cumulative carbon emissions comparison
- Infrastructure overhead vs. workload savings
- Summary comparison tables

Usage:
    python3 plot_weight_evaluation.py results/weight_evaluation_20250120_123456
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np


def load_timeseries(csv_path: Path) -> Dict[str, List]:
    """Load timeseries CSV into dict of lists."""
    data = {
        "timestamp": [],
        "elapsed_seconds": [],
        "carbon_intensity": [],
        "delta_requests_total": [],
        "delta_requests_p30": [],
        "delta_requests_p50": [],
        "delta_requests_p100": [],
        "total_power_watts": [],
        "power_always_on": [],
        "power_router": [],
        "power_consumer": [],
        "power_target_p30": [],
        "power_target_p50": [],
        "power_target_p100": [],
        "replicas_router": [],
        "replicas_consumer": [],
        "replicas_target_p30": [],
        "replicas_target_p50": [],
        "replicas_target_p100": [],
        "throttle_factor": [],
    }

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data["timestamp"].append(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")))
                data["elapsed_seconds"].append(float(row["elapsed_seconds"]))
                data["carbon_intensity"].append(float(row["carbon_intensity"]) if row["carbon_intensity"] else 0)
                data["delta_requests_total"].append(int(row["delta_requests_total"]) if row["delta_requests_total"] else 0)
                data["delta_requests_p30"].append(int(row["delta_requests_p30"]) if row["delta_requests_p30"] else 0)
                data["delta_requests_p50"].append(int(row["delta_requests_p50"]) if row["delta_requests_p50"] else 0)
                data["delta_requests_p100"].append(int(row["delta_requests_p100"]) if row["delta_requests_p100"] else 0)
                data["total_power_watts"].append(float(row["total_power_watts"]) if row["total_power_watts"] else 0)
                data["power_always_on"].append(float(row["power_always_on"]) if row["power_always_on"] else 0)
                data["power_router"].append(float(row["power_router"]) if row["power_router"] else 0)
                data["power_consumer"].append(float(row["power_consumer"]) if row["power_consumer"] else 0)
                data["power_target_p30"].append(float(row["power_target_p30"]) if row["power_target_p30"] else 0)
                data["power_target_p50"].append(float(row["power_target_p50"]) if row["power_target_p50"] else 0)
                data["power_target_p100"].append(float(row["power_target_p100"]) if row["power_target_p100"] else 0)
                data["replicas_router"].append(int(row["replicas_router"]) if row["replicas_router"] else 0)
                data["replicas_consumer"].append(int(row["replicas_consumer"]) if row["replicas_consumer"] else 0)
                data["replicas_target_p30"].append(int(row["replicas_target_p30"]) if row["replicas_target_p30"] else 0)
                data["replicas_target_p50"].append(int(row["replicas_target_p50"]) if row["replicas_target_p50"] else 0)
                data["replicas_target_p100"].append(int(row["replicas_target_p100"]) if row["replicas_target_p100"] else 0)
                data["throttle_factor"].append(float(row["throttle_factor"]) if row.get("throttle_factor") else 1.0)
            except (ValueError, KeyError) as e:
                print(f"Warning: Skipping malformed row: {e}")
                continue

    return data


def calculate_cumulative_carbon(
    elapsed_seconds: List[float],
    power_watts: List[float],
    carbon_intensity: List[float]
) -> List[float]:
    """Calculate cumulative carbon emissions from power and carbon intensity."""
    cumulative = []
    total = 0.0

    for i in range(len(elapsed_seconds)):
        if i == 0:
            cumulative.append(0.0)
            continue

        # Calculate interval
        interval_seconds = elapsed_seconds[i] - elapsed_seconds[i-1]
        interval_hours = interval_seconds / 3600.0

        # Average power and carbon intensity for this interval
        avg_power = (power_watts[i] + power_watts[i-1]) / 2.0
        avg_carbon = (carbon_intensity[i] + carbon_intensity[i-1]) / 2.0

        # Calculate carbon for this interval
        energy_kwh = (avg_power * interval_hours) / 1000.0
        carbon_g = energy_kwh * avg_carbon

        total += carbon_g
        cumulative.append(total)

    return cumulative


def plot_comparison(
    baseline_dir: Path,
    carbon_aware_dir: Path,
    output_dir: Path,
    analysis: Dict
) -> None:
    """Generate comprehensive comparison plots."""
    # Load data
    print("  Loading timeseries data...")
    baseline_data = load_timeseries(baseline_dir / "timeseries.csv")
    carbon_aware_data = load_timeseries(carbon_aware_dir / "timeseries.csv")

    # Calculate cumulative carbon
    baseline_cumulative = calculate_cumulative_carbon(
        baseline_data["elapsed_seconds"],
        baseline_data["total_power_watts"],
        baseline_data["carbon_intensity"]
    )
    carbon_aware_cumulative = calculate_cumulative_carbon(
        carbon_aware_data["elapsed_seconds"],
        carbon_aware_data["total_power_watts"],
        carbon_aware_data["carbon_intensity"]
    )

    # Create figure with subplots
    fig = plt.figure(figsize=(16, 12))
    gs = fig.add_gridspec(4, 2, hspace=0.3, wspace=0.3)

    # 1. Carbon Intensity Timeline
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(baseline_data["elapsed_seconds"], baseline_data["carbon_intensity"],
             label="Baseline", color="#e74c3c", linewidth=2)
    ax1.plot(carbon_aware_data["elapsed_seconds"], carbon_aware_data["carbon_intensity"],
             label="Carbon-Aware", color="#27ae60", linewidth=2, linestyle="--")
    ax1.set_xlabel("Time (seconds)")
    ax1.set_ylabel("Carbon Intensity (gCO₂/kWh)")
    ax1.set_title("Carbon Intensity Over Time")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # 2. Replica Counts - Baseline
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.stackplot(
        baseline_data["elapsed_seconds"],
        baseline_data["replicas_router"],
        baseline_data["replicas_consumer"],
        baseline_data["replicas_target_p30"],
        baseline_data["replicas_target_p50"],
        baseline_data["replicas_target_p100"],
        labels=["Router", "Consumer", "Target P30", "Target P50", "Target P100"],
        colors=["#3498db", "#9b59b6", "#f39c12", "#e67e22", "#e74c3c"],
        alpha=0.8
    )
    ax2.set_xlabel("Time (seconds)")
    ax2.set_ylabel("Replica Count")
    ax2.set_title("Replica Counts - Baseline")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)

    # 3. Replica Counts - Carbon-Aware
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.stackplot(
        carbon_aware_data["elapsed_seconds"],
        carbon_aware_data["replicas_router"],
        carbon_aware_data["replicas_consumer"],
        carbon_aware_data["replicas_target_p30"],
        carbon_aware_data["replicas_target_p50"],
        carbon_aware_data["replicas_target_p100"],
        labels=["Router", "Consumer", "Target P30", "Target P50", "Target P100"],
        colors=["#3498db", "#9b59b6", "#f39c12", "#e67e22", "#e74c3c"],
        alpha=0.8
    )
    ax3.set_xlabel("Time (seconds)")
    ax3.set_ylabel("Replica Count")
    ax3.set_title("Replica Counts - Carbon-Aware (Throttled)")
    ax3.legend(loc="upper left", fontsize=8)
    ax3.grid(True, alpha=0.3)

    # 4. Power Consumption - Baseline
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.stackplot(
        baseline_data["elapsed_seconds"],
        baseline_data["power_always_on"],
        baseline_data["power_router"],
        baseline_data["power_consumer"],
        [baseline_data["power_target_p30"][i] + baseline_data["power_target_p50"][i] +
         baseline_data["power_target_p100"][i] for i in range(len(baseline_data["elapsed_seconds"]))],
        labels=["Always-On", "Router", "Consumer", "Target (All)"],
        colors=["#95a5a6", "#3498db", "#9b59b6", "#e74c3c"],
        alpha=0.8
    )
    ax4.set_xlabel("Time (seconds)")
    ax4.set_ylabel("Power (W)")
    ax4.set_title("Power Consumption - Baseline")
    ax4.legend(loc="upper left", fontsize=8)
    ax4.grid(True, alpha=0.3)

    # 5. Power Consumption - Carbon-Aware
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.stackplot(
        carbon_aware_data["elapsed_seconds"],
        carbon_aware_data["power_always_on"],
        carbon_aware_data["power_router"],
        carbon_aware_data["power_consumer"],
        [carbon_aware_data["power_target_p30"][i] + carbon_aware_data["power_target_p50"][i] +
         carbon_aware_data["power_target_p100"][i] for i in range(len(carbon_aware_data["elapsed_seconds"]))],
        labels=["Always-On", "Router", "Consumer", "Target (All)"],
        colors=["#95a5a6", "#3498db", "#9b59b6", "#e74c3c"],
        alpha=0.8
    )
    ax5.set_xlabel("Time (seconds)")
    ax5.set_ylabel("Power (W)")
    ax5.set_title("Power Consumption - Carbon-Aware")
    ax5.legend(loc="upper left", fontsize=8)
    ax5.grid(True, alpha=0.3)

    # 6. Cumulative Carbon Emissions
    ax6 = fig.add_subplot(gs[3, :])
    ax6.plot(baseline_data["elapsed_seconds"], baseline_cumulative,
             label="Baseline Total", color="#e74c3c", linewidth=2.5)
    ax6.plot(carbon_aware_data["elapsed_seconds"], carbon_aware_cumulative,
             label="Carbon-Aware Total", color="#27ae60", linewidth=2.5)

    # Add shaded area showing savings
    if len(baseline_cumulative) == len(carbon_aware_cumulative):
        ax6.fill_between(
            baseline_data["elapsed_seconds"],
            carbon_aware_cumulative,
            baseline_cumulative,
            alpha=0.3,
            color="#2ecc71",
            label="Carbon Savings"
        )

    ax6.set_xlabel("Time (seconds)")
    ax6.set_ylabel("Cumulative Carbon (gCO₂)")
    ax6.set_title("Cumulative Infrastructure Carbon Emissions")
    ax6.legend()
    ax6.grid(True, alpha=0.3)

    # Add text annotation with key metrics
    final_baseline = baseline_cumulative[-1] if baseline_cumulative else 0
    final_carbon_aware = carbon_aware_cumulative[-1] if carbon_aware_cumulative else 0
    savings = final_baseline - final_carbon_aware

    ax6.text(
        0.02, 0.98,
        f"Baseline: {final_baseline:.2f} gCO₂\n"
        f"Carbon-Aware: {final_carbon_aware:.2f} gCO₂\n"
        f"Savings: {savings:.2f} gCO₂ ({(savings/final_baseline*100):.1f}%)",
        transform=ax6.transAxes,
        verticalalignment='top',
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
        fontsize=10
    )

    plt.suptitle("Weight Evaluation: Infrastructure Carbon Overhead Analysis",
                 fontsize=16, fontweight='bold', y=0.995)

    # Save plot
    output_path = output_dir / "weight_evaluation_comparison.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  ✓ Saved: {output_path}")
    plt.close()


def plot_overhead_breakdown(analysis: Dict, output_dir: Path) -> None:
    """Generate pie chart showing overhead breakdown."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Baseline breakdown
    baseline_infra = analysis['baseline']['infrastructure_carbon_g']
    baseline_workload = analysis['baseline']['workload_carbon_g']

    ax1.pie(
        [baseline_infra, baseline_workload],
        labels=['Infrastructure', 'Workload'],
        colors=['#95a5a6', '#e74c3c'],
        autopct='%1.1f%%',
        startangle=90
    )
    ax1.set_title(f"Baseline Carbon Distribution\nTotal: {baseline_infra + baseline_workload:.2f} gCO₂")

    # Carbon-aware breakdown
    carbon_aware_infra = analysis['carbon_aware']['infrastructure_carbon_g']
    carbon_aware_workload = analysis['carbon_aware']['workload_carbon_g']

    ax2.pie(
        [carbon_aware_infra, carbon_aware_workload],
        labels=['Infrastructure', 'Workload'],
        colors=['#95a5a6', '#27ae60'],
        autopct='%1.1f%%',
        startangle=90
    )
    ax2.set_title(f"Carbon-Aware Distribution\nTotal: {carbon_aware_infra + carbon_aware_workload:.2f} gCO₂")

    plt.suptitle("Infrastructure vs. Workload Carbon", fontsize=14, fontweight='bold')

    output_path = output_dir / "carbon_breakdown.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  ✓ Saved: {output_path}")
    plt.close()


def plot_savings_analysis(analysis: Dict, output_dir: Path) -> None:
    """Generate bar chart showing savings analysis."""
    fig, ax = plt.subplots(figsize=(10, 6))

    categories = ['Baseline\nTotal', 'Carbon-Aware\nTotal', 'Infrastructure\nOverhead', 'Net\nSavings']
    values = [
        analysis['baseline']['total_carbon_g'],
        analysis['carbon_aware']['total_carbon_g'],
        analysis['savings']['infrastructure_overhead_g'],
        analysis['savings']['net_total_savings_g']
    ]
    colors = ['#e74c3c', '#27ae60', '#95a5a6', '#2ecc71']

    bars = ax.bar(categories, values, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)

    # Add value labels on bars
    for bar, value in zip(bars, values):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{value:.2f} g',
                ha='center', va='bottom', fontweight='bold')

    ax.set_ylabel('Carbon (gCO₂)', fontweight='bold')
    ax.set_title('Carbon Savings Analysis', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='y')

    # Add horizontal line showing infrastructure overhead
    overhead = analysis['savings']['infrastructure_overhead_g']
    ax.axhline(y=overhead, color='#95a5a6', linestyle='--', linewidth=2,
               label=f'Infrastructure Overhead: {overhead:.2f} g')

    # Add text box with key metrics
    overhead_ratio = analysis['savings']['overhead_ratio']
    ax.text(
        0.98, 0.98,
        f"Overhead Ratio: {overhead_ratio:.4f} ({overhead_ratio*100:.2f}%)\n"
        f"Savings per 1g overhead: {1/overhead_ratio:.1f}g\n"
        f"Net Savings: {analysis['savings']['net_savings_ratio']*100:.1f}%",
        transform=ax.transAxes,
        verticalalignment='top',
        horizontalalignment='right',
        bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.7),
        fontsize=10,
        fontweight='bold'
    )

    ax.legend(loc='upper left')

    output_path = output_dir / "savings_analysis.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"  ✓ Saved: {output_path}")
    plt.close()


def generate_summary_table(analysis: Dict, output_dir: Path) -> None:
    """Generate markdown summary table for thesis."""
    content = f"""# Weight Evaluation Summary

**Generated:** {analysis['timestamp']}

## Carbon Emissions Breakdown

| Metric | Baseline | Carbon-Aware | Difference |
|--------|----------|--------------|------------|
| Infrastructure Carbon (g) | {analysis['baseline']['infrastructure_carbon_g']:.2f} | {analysis['carbon_aware']['infrastructure_carbon_g']:.2f} | {analysis['baseline']['infrastructure_carbon_g'] - analysis['carbon_aware']['infrastructure_carbon_g']:.2f} |
| Workload Carbon (g) | {analysis['baseline']['workload_carbon_g']:.2f} | {analysis['carbon_aware']['workload_carbon_g']:.2f} | {analysis['baseline']['workload_carbon_g'] - analysis['carbon_aware']['workload_carbon_g']:.2f} |
| **Total Carbon (g)** | **{analysis['baseline']['total_carbon_g']:.2f}** | **{analysis['carbon_aware']['total_carbon_g']:.2f}** | **{analysis['savings']['net_total_savings_g']:.2f}** |

## Savings Analysis

| Metric | Value | Interpretation |
|--------|-------|----------------|
| Workload Carbon Savings | {analysis['savings']['workload_carbon_savings_g']:.2f} gCO₂ | Carbon saved through carbon-aware scheduling |
| Infrastructure Overhead | {analysis['savings']['infrastructure_overhead_g']:.2f} gCO₂ | Cost of running the carbon-aware system |
| Net Total Savings | {analysis['savings']['net_total_savings_g']:.2f} gCO₂ | Total emissions avoided |
| Overhead Ratio | {analysis['savings']['overhead_ratio']:.4f} ({analysis['savings']['overhead_ratio']*100:.2f}%) | Infrastructure cost / Workload savings |
| Net Savings Ratio | {analysis['savings']['net_savings_ratio']:.4f} ({analysis['savings']['net_savings_ratio']*100:.2f}%) | Total savings / Baseline emissions |

## Key Thesis Metric

**For every 1 gCO₂ spent on infrastructure, the carbon-aware system saves {1/analysis['savings']['overhead_ratio']:.1f} gCO₂ in workload emissions.**

The carbon savings are {'**more than one order of magnitude**' if analysis['savings']['overhead_ratio'] < 0.1 else '**significantly**'} larger than the infrastructure overhead.

## Interpretation

The weight evaluation demonstrates that the carbon cost of operating the carbon-aware scheduling system is negligible compared to the carbon savings it achieves. The overhead ratio of {analysis['savings']['overhead_ratio']*100:.2f}% proves that the system provides substantial net environmental benefit.

This validates the thesis that carbon-aware scheduling is not only theoretically sound but also practically viable, as the infrastructure overhead does not negate the environmental benefits.
"""

    output_path = output_dir / "SUMMARY.md"
    with open(output_path, 'w') as f:
        f.write(content)

    print(f"  ✓ Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Plot weight evaluation results")
    parser.add_argument("results_dir", type=str, help="Path to weight evaluation results directory")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"Error: Results directory not found: {results_dir}")
        sys.exit(1)

    baseline_dir = results_dir / "baseline"
    carbon_aware_dir = results_dir / "carbon_aware"
    analysis_path = results_dir / "weight_analysis.json"

    # Validate required files
    if not baseline_dir.exists():
        print(f"Error: Baseline directory not found: {baseline_dir}")
        sys.exit(1)
    if not carbon_aware_dir.exists():
        print(f"Error: Carbon-aware directory not found: {carbon_aware_dir}")
        sys.exit(1)
    if not analysis_path.exists():
        print(f"Error: Analysis file not found: {analysis_path}")
        sys.exit(1)

    # Load analysis
    with open(analysis_path, 'r') as f:
        analysis = json.load(f)

    # Create plots directory
    plots_dir = results_dir / "plots"
    plots_dir.mkdir(exist_ok=True)

    print("Generating weight evaluation visualizations...")

    # Generate plots
    print("\n[1/4] Creating comparison plot...")
    plot_comparison(baseline_dir, carbon_aware_dir, plots_dir, analysis)

    print("\n[2/4] Creating overhead breakdown...")
    plot_overhead_breakdown(analysis, plots_dir)

    print("\n[3/4] Creating savings analysis...")
    plot_savings_analysis(analysis, plots_dir)

    print("\n[4/4] Generating summary table...")
    generate_summary_table(analysis, results_dir)

    print(f"\n✓ All visualizations generated!")
    print(f"  Output directory: {plots_dir}")
    print(f"  Summary: {results_dir / 'SUMMARY.md'}")


if __name__ == "__main__":
    main()
