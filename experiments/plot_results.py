#!/usr/bin/env python3
"""
Plot comprehensive graphs from temporal benchmark results.

Generates:
- Precision over time (per policy)
- Carbon intensity over time (per policy)
- Credit balance over time (per policy)
- Comparison plots across policies
"""

import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import sys

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except ImportError:
    print("Error: matplotlib not installed. Install with: pip3 install matplotlib")
    sys.exit(1)

RESULTS_DIR = Path(__file__).parent / "results"
PLOTS_DIR = Path(__file__).parent / "plots"

# Style configuration
POLICY_COLORS = {
    "credit-greedy": "#2E86AB",
    "forecast-aware": "#A23B72",
    "forecast-aware-global": "#F18F01",
    "precision-tier": "#C73E1D",
}

POLICY_LABELS = {
    "credit-greedy": "Credit Greedy",
    "forecast-aware": "Forecast Aware",
    "forecast-aware-global": "Forecast Aware Global",
    "precision-tier": "Precision Tier",
}


def load_timeseries(policy: str) -> Tuple[List[float], List[int], List[float], List[float], List[float], List[float]]:
    """
    Load timeseries data from CSV.
    
    Returns:
        (elapsed_times, requests, precisions, credits, carbon_now, carbon_next)
    """
    csv_path = RESULTS_DIR / policy / "timeseries.csv"
    if not csv_path.exists():
        print(f"  ⚠ No timeseries data for {policy}")
        return [], [], [], [], [], []
    
    elapsed = []
    requests = []
    precisions = []
    credits = []
    carbon_now = []
    carbon_next = []
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                elapsed.append(float(row["elapsed_minutes"]))
                requests.append(int(row["requests_delta"]) if row["requests_delta"] else 0)
                precisions.append(float(row["precision"]) if row["precision"] else 0.0)
                credits.append(float(row["credit_balance"]) if row["credit_balance"] else 0.0)
                carbon_now.append(float(row["carbon_now"]) if row["carbon_now"] else 0.0)
                carbon_next.append(float(row["carbon_next"]) if row["carbon_next"] else 0.0)
            except (ValueError, KeyError) as e:
                print(f"  ⚠ Skipping malformed row in {policy}: {e}")
                continue
    
    return elapsed, requests, precisions, credits, carbon_now, carbon_next


def load_summary(policy: str) -> Dict:
    """Load summary.json for a policy."""
    summary_path = RESULTS_DIR / policy / "summary.json"
    if not summary_path.exists():
        return {}
    
    with open(summary_path, "r", encoding="utf-8") as f:
        return json.load(f)


def plot_individual_policy(policy: str) -> None:
    """Generate individual plots for a single policy."""
    print(f"\n📊 Plotting {policy}...")
    
    elapsed, requests, precisions, credits, carbon_now, carbon_next = load_timeseries(policy)
    
    if not elapsed:
        print(f"  ⚠ No data to plot for {policy}")
        return
    
    policy_plot_dir = PLOTS_DIR / policy
    policy_plot_dir.mkdir(parents=True, exist_ok=True)
    
    color = POLICY_COLORS.get(policy, "#333333")
    label = POLICY_LABELS.get(policy, policy)
    
    # 1. Precision over time
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(elapsed, precisions, color=color, linewidth=2, marker='o', markersize=4, label=label)
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Precision", fontsize=12)
    ax.set_title(f"Precision Over Time - {label}", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(policy_plot_dir / "precision.png", dpi=150)
    plt.close(fig)
    
    # 2. Carbon intensity over time (now vs next)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(elapsed, carbon_now, color="#E63946", linewidth=2, marker='s', markersize=4, label="Current")
    ax.plot(elapsed, carbon_next, color="#06AED5", linewidth=2, marker='^', markersize=4, label="Next Period")
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Carbon Intensity (gCO₂/kWh)", fontsize=12)
    ax.set_title(f"Carbon Intensity Over Time - {label}", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(policy_plot_dir / "carbon.png", dpi=150)
    plt.close(fig)
    
    # 3. Credit balance over time
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(elapsed, credits, color="#06A77D", linewidth=2, marker='d', markersize=4, label="Credit Balance")
    ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Credit Balance", fontsize=12)
    ax.set_title(f"Credit Balance Over Time - {label}", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(policy_plot_dir / "credits.png", dpi=150)
    plt.close(fig)
    
    # 4. Request rate over time
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(elapsed, requests, color=color, linewidth=2, marker='o', markersize=4, label=label)
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Requests per Sample Period", fontsize=12)
    ax.set_title(f"Request Rate Over Time - {label}", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(policy_plot_dir / "request_rate.png", dpi=150)
    plt.close(fig)
    
    print(f"  ✓ Saved 4 plots to {policy_plot_dir}")


def plot_comparison(policies: List[str]) -> None:
    """Generate comparison plots across all policies."""
    print(f"\n📊 Plotting comparisons across {len(policies)} policies...")
    
    comparison_dir = PLOTS_DIR / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Precision comparison
    fig, ax = plt.subplots(figsize=(12, 6))
    for policy in policies:
        elapsed, _, precisions, _, _, _ = load_timeseries(policy)
        if not elapsed:
            continue
        color = POLICY_COLORS.get(policy, "#333333")
        label = POLICY_LABELS.get(policy, policy)
        ax.plot(elapsed, precisions, color=color, linewidth=2, marker='o', markersize=3, label=label)
    
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Precision", fontsize=12)
    ax.set_title("Precision Comparison Across Policies", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    fig.tight_layout()
    fig.savefig(comparison_dir / "precision_comparison.png", dpi=150)
    plt.close(fig)
    
    # 2. Credit balance comparison
    fig, ax = plt.subplots(figsize=(12, 6))
    for policy in policies:
        elapsed, _, _, credits, _, _ = load_timeseries(policy)
        if not elapsed:
            continue
        color = POLICY_COLORS.get(policy, "#333333")
        label = POLICY_LABELS.get(policy, policy)
        ax.plot(elapsed, credits, color=color, linewidth=2, marker='d', markersize=3, label=label)
    
    ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Credit Balance", fontsize=12)
    ax.set_title("Credit Balance Comparison Across Policies", fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best')
    fig.tight_layout()
    fig.savefig(comparison_dir / "credits_comparison.png", dpi=150)
    plt.close(fig)
    
    # 3. Summary bar chart (mean values)
    summaries = {policy: load_summary(policy) for policy in policies}
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    # Mean precision
    policy_names = [POLICY_LABELS.get(p, p) for p in policies if summaries[p]]
    mean_precisions = [summaries[p].get("mean_precision", 0) for p in policies if summaries[p]]
    colors = [POLICY_COLORS.get(p, "#333333") for p in policies if summaries[p]]
    
    axes[0].bar(policy_names, mean_precisions, color=colors)
    axes[0].set_ylabel("Mean Precision", fontsize=11)
    axes[0].set_title("Mean Precision", fontsize=12, fontweight='bold')
    axes[0].tick_params(axis='x', rotation=15)
    axes[0].grid(axis='y', alpha=0.3)
    
    # Total requests
    total_requests = [summaries[p].get("total_requests", 0) for p in policies if summaries[p]]
    axes[1].bar(policy_names, total_requests, color=colors)
    axes[1].set_ylabel("Total Requests", fontsize=11)
    axes[1].set_title("Total Requests Processed", fontsize=12, fontweight='bold')
    axes[1].tick_params(axis='x', rotation=15)
    axes[1].grid(axis='y', alpha=0.3)
    
    # Mean carbon intensity
    mean_carbon = [summaries[p].get("mean_carbon_intensity", 0) for p in policies if summaries[p]]
    axes[2].bar(policy_names, mean_carbon, color=colors)
    axes[2].set_ylabel("Carbon Intensity (gCO₂/kWh)", fontsize=11)
    axes[2].set_title("Mean Carbon Intensity", fontsize=12, fontweight='bold')
    axes[2].tick_params(axis='x', rotation=15)
    axes[2].grid(axis='y', alpha=0.3)
    
    fig.tight_layout()
    fig.savefig(comparison_dir / "summary_comparison.png", dpi=150)
    plt.close(fig)
    
    print(f"  ✓ Saved comparison plots to {comparison_dir}")


def main():
    """Main execution."""
    print("=" * 60)
    print("Temporal Benchmark Result Plotter")
    print("=" * 60)
    
    if not RESULTS_DIR.exists():
        print(f"\n❌ Results directory not found: {RESULTS_DIR}")
        print("   Run the benchmark first: python3 run_temporal_benchmark.py")
        return 1
    
    # Find all policies with results
    policies = [
        p.name for p in RESULTS_DIR.iterdir()
        if p.is_dir() and (p / "timeseries.csv").exists()
    ]
    
    if not policies:
        print(f"\n❌ No timeseries data found in {RESULTS_DIR}")
        return 1
    
    print(f"\nFound {len(policies)} policies with results: {', '.join(policies)}")
    
    # Create plots directory
    PLOTS_DIR.mkdir(exist_ok=True)
    
    # Generate individual plots
    for policy in policies:
        plot_individual_policy(policy)
    
    # Generate comparison plots
    if len(policies) > 1:
        plot_comparison(policies)
    else:
        print("\n⚠ Only one policy found, skipping comparison plots")
    
    print("\n" + "=" * 60)
    print(f"✅ All plots saved to: {PLOTS_DIR}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
