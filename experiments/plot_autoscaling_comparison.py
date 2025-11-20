#!/usr/bin/env python3
"""
Plot autoscaling benchmark comparison: throttling vs no-throttling.

Generates multi-panel plots showing:
- Replica counts (consumer + target) over time
- Queue depths over time
- Throttle factor and replica ceilings over time
- Carbon intensity and cumulative emissions
- Request rates and precision

Usage:
    python3 plot_autoscaling_comparison.py results/autoscaling_20250120_123456
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


def load_timeseries(csv_path: Path) -> Dict[str, List]:
    """Load timeseries CSV into dict of lists."""
    data = {
        "timestamp": [],
        "elapsed_seconds": [],
        "delta_requests": [],
        "mean_precision": [],
        "credit_balance": [],
        "carbon_now": [],
        "queue_depth_total": [],
        "replicas_consumer": [],
        "replicas_target": [],
        "ceiling_consumer": [],
        "ceiling_target": [],
        "throttle_factor": [],
    }

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data["timestamp"].append(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")))
                data["elapsed_seconds"].append(float(row["elapsed_seconds"]))
                data["delta_requests"].append(int(row["delta_requests"]) if row["delta_requests"] else 0)
                data["mean_precision"].append(float(row["mean_precision"]) if row["mean_precision"] else None)
                data["credit_balance"].append(float(row["credit_balance"]) if row["credit_balance"] else None)
                data["carbon_now"].append(float(row["carbon_now"]) if row["carbon_now"] else None)
                data["queue_depth_total"].append(int(row["queue_depth_total"]) if row["queue_depth_total"] else 0)
                data["replicas_consumer"].append(int(row["replicas_consumer"]) if row["replicas_consumer"] else 0)
                data["replicas_target"].append(int(row["replicas_target"]) if row["replicas_target"] else 0)
                data["ceiling_consumer"].append(int(row["ceiling_consumer"]) if row["ceiling_consumer"] else None)
                data["ceiling_target"].append(int(row["ceiling_target"]) if row["ceiling_target"] else None)
                data["throttle_factor"].append(float(row["throttle_factor"]) if row["throttle_factor"] else None)
            except (ValueError, KeyError) as e:
                print(f"Warning: Skipping malformed row: {e}")
                continue

    return data


def calculate_cumulative_carbon(data: Dict[str, List], carbon_map: Dict[str, float]) -> List[float]:
    """Calculate cumulative carbon emissions from request deltas."""
    cumulative = []
    total = 0.0

    # For simplicity, assume uniform carbon intensity across flavours
    # In reality, this should weight by flavour carbon intensity
    for i, requests in enumerate(data["delta_requests"]):
        if requests > 0 and data["carbon_now"][i] is not None:
            # Rough estimate: requests * carbon_intensity * energy_per_request
            # We'll just use carbon_now as a proxy
            total += requests * data["carbon_now"][i] * 0.001  # Scale factor
        cumulative.append(total)

    return cumulative


def plot_comparison(
    data_throttle: Dict[str, List],
    data_no_throttle: Dict[str, List],
    output_dir: Path
) -> None:
    """Generate comparison plots."""
    fig, axes = plt.subplots(5, 1, figsize=(14, 18), sharex=True)
    fig.suptitle("Autoscaling Benchmark: Throttling vs No-Throttling", fontsize=16, fontweight="bold")

    # Panel 1: Replica Counts
    ax = axes[0]
    ax.set_title("Replica Counts (Consumer + Target)", fontweight="bold")
    ax.set_ylabel("Replicas")
    ax.grid(True, alpha=0.3)

    # Throttle strategy
    replicas_throttle = [c + t for c, t in zip(data_throttle["replicas_consumer"], data_throttle["replicas_target"])]
    ax.plot(data_throttle["elapsed_seconds"], replicas_throttle,
            label="With Throttling", linewidth=2, color="green", alpha=0.8)

    # No-throttle strategy
    replicas_no_throttle = [c + t for c, t in zip(data_no_throttle["replicas_consumer"], data_no_throttle["replicas_target"])]
    ax.plot(data_no_throttle["elapsed_seconds"], replicas_no_throttle,
            label="Without Throttling", linewidth=2, color="orange", alpha=0.8)

    ax.legend(loc="upper right")

    # Panel 2: Queue Depths
    ax = axes[1]
    ax.set_title("RabbitMQ Queue Depth (Total Messages Ready)", fontweight="bold")
    ax.set_ylabel("Messages")
    ax.grid(True, alpha=0.3)

    ax.plot(data_throttle["elapsed_seconds"], data_throttle["queue_depth_total"],
            label="With Throttling", linewidth=2, color="green", alpha=0.8)
    ax.plot(data_no_throttle["elapsed_seconds"], data_no_throttle["queue_depth_total"],
            label="Without Throttling", linewidth=2, color="orange", alpha=0.8)

    ax.legend(loc="upper right")

    # Panel 3: Throttle Factor
    ax = axes[2]
    ax.set_title("Throttle Factor & Carbon Intensity", fontweight="bold")
    ax.set_ylabel("Throttle Factor", color="blue")
    ax.tick_params(axis='y', labelcolor="blue")
    ax.grid(True, alpha=0.3)

    # Plot throttle factors
    throttle_valid = [(e, t) for e, t in zip(data_throttle["elapsed_seconds"], data_throttle["throttle_factor"]) if t is not None]
    if throttle_valid:
        elapsed_t, throttle_t = zip(*throttle_valid)
        ax.plot(elapsed_t, throttle_t, label="Throttle (with)", linewidth=2, color="blue", alpha=0.8)

    throttle_no_valid = [(e, t) for e, t in zip(data_no_throttle["elapsed_seconds"], data_no_throttle["throttle_factor"]) if t is not None]
    if throttle_no_valid:
        elapsed_nt, throttle_nt = zip(*throttle_no_valid)
        ax.plot(elapsed_nt, throttle_nt, label="Throttle (without)", linewidth=2, color="orange", linestyle="--", alpha=0.8)

    ax.legend(loc="upper left")

    # Overlay carbon intensity on secondary y-axis
    ax2 = ax.twinx()
    ax2.set_ylabel("Carbon Intensity (gCO₂/kWh)", color="red")
    ax2.tick_params(axis='y', labelcolor="red")

    carbon_valid = [(e, c) for e, c in zip(data_throttle["elapsed_seconds"], data_throttle["carbon_now"]) if c is not None]
    if carbon_valid:
        elapsed_c, carbon_c = zip(*carbon_valid)
        ax2.plot(elapsed_c, carbon_c, label="Carbon Intensity", linewidth=2, color="red", alpha=0.5)

    # Panel 4: Cumulative Carbon Emissions (approximation)
    ax = axes[3]
    ax.set_title("Request Rate", fontweight="bold")
    ax.set_ylabel("Requests / Sample")
    ax.grid(True, alpha=0.3)

    ax.plot(data_throttle["elapsed_seconds"], data_throttle["delta_requests"],
            label="With Throttling", linewidth=2, color="green", alpha=0.8)
    ax.plot(data_no_throttle["elapsed_seconds"], data_no_throttle["delta_requests"],
            label="Without Throttling", linewidth=2, color="orange", alpha=0.8)

    ax.legend(loc="upper right")

    # Panel 5: Mean Precision
    ax = axes[4]
    ax.set_title("Mean Precision", fontweight="bold")
    ax.set_xlabel("Elapsed Time (seconds)")
    ax.set_ylabel("Precision")
    ax.grid(True, alpha=0.3)

    prec_throttle_valid = [(e, p) for e, p in zip(data_throttle["elapsed_seconds"], data_throttle["mean_precision"]) if p is not None]
    if prec_throttle_valid:
        elapsed_pt, prec_t = zip(*prec_throttle_valid)
        ax.plot(elapsed_pt, prec_t, label="With Throttling", linewidth=2, color="green", alpha=0.8)

    prec_no_throttle_valid = [(e, p) for e, p in zip(data_no_throttle["elapsed_seconds"], data_no_throttle["mean_precision"]) if p is not None]
    if prec_no_throttle_valid:
        elapsed_pnt, prec_nt = zip(*prec_no_throttle_valid)
        ax.plot(elapsed_pnt, prec_nt, label="Without Throttling", linewidth=2, color="orange", alpha=0.8)

    ax.legend(loc="lower right")

    plt.tight_layout()
    output_path = output_dir / "autoscaling_comparison.png"
    plt.savefig(output_path, dpi=150)
    print(f"✓ Saved comparison plot: {output_path}")
    plt.close()


def print_summary(summaries: List[Dict]) -> None:
    """Print summary comparison."""
    print("\n" + "="*70)
    print("SUMMARY COMPARISON")
    print("="*70)

    for summary in summaries:
        policy = summary.get("policy", "unknown")
        config = summary.get("config_overrides", {})
        total_requests = summary.get("total_requests", 0)
        mean_precision = summary.get("mean_precision", 0)
        mean_carbon = summary.get("mean_carbon_intensity", 0)

        print(f"\n{policy}:")
        print(f"  Config: {config}")
        print(f"  Total requests: {total_requests:.0f}")
        print(f"  Mean precision: {mean_precision:.4f}")
        print(f"  Mean carbon intensity: {mean_carbon:.4f}")

    if len(summaries) == 2:
        carbon_0 = summaries[0].get("mean_carbon_intensity", 0)
        carbon_1 = summaries[1].get("mean_carbon_intensity", 0)

        if carbon_1 > 0:
            carbon_savings = (1 - carbon_0 / carbon_1) * 100
            print(f"\n💚 Carbon savings (throttling vs no-throttling): {carbon_savings:+.1f}%")


def main():
    parser = argparse.ArgumentParser(
        description="Plot autoscaling benchmark comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Results directory (e.g., results/autoscaling_20250120_123456)"
    )

    args = parser.parse_args()
    results_dir = args.results_dir

    if not results_dir.exists():
        print(f"❌ Results directory not found: {results_dir}")
        return 1

    # Find strategy subdirectories
    strategy_dirs = [d for d in results_dir.iterdir() if d.is_dir()]

    if len(strategy_dirs) < 2:
        print(f"❌ Expected 2 strategy directories, found {len(strategy_dirs)}")
        return 1

    print(f"Loading results from: {results_dir}")

    # Identify throttle vs no-throttle strategies
    data_throttle = None
    data_no_throttle = None

    for strategy_dir in strategy_dirs:
        csv_path = strategy_dir / "timeseries.csv"
        if not csv_path.exists():
            print(f"⚠️  Missing timeseries.csv in {strategy_dir.name}")
            continue

        if "no-throttle" in strategy_dir.name:
            print(f"  Loading no-throttle: {strategy_dir.name}")
            data_no_throttle = load_timeseries(csv_path)
        else:
            print(f"  Loading throttle: {strategy_dir.name}")
            data_throttle = load_timeseries(csv_path)

    if data_throttle is None or data_no_throttle is None:
        print("❌ Could not load both strategy results")
        return 1

    print("\n✓ Data loaded successfully")
    print(f"  With throttling: {len(data_throttle['elapsed_seconds'])} samples")
    print(f"  Without throttling: {len(data_no_throttle['elapsed_seconds'])} samples")

    # Generate plots
    print("\n📊 Generating comparison plots...")
    plot_comparison(data_throttle, data_no_throttle, results_dir)

    # Load and print summaries
    comparison_path = results_dir / "comparison.json"
    if comparison_path.exists():
        with open(comparison_path, "r", encoding="utf-8") as f:
            comparison = json.load(f)
            summaries = comparison.get("strategies", [])
            print_summary(summaries)

    print("\n✅ Visualization complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
