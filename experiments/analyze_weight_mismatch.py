#!/usr/bin/env python3
"""Analyze commanded vs actual weight mismatch in benchmark results."""

import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple

def analyze_mismatch(csv_path: Path) -> None:
    """Analyze weight mismatch from timeseries CSV."""

    print(f"Analyzing: {csv_path}")
    print("=" * 80)

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_deviation_p30 = 0.0
    total_deviation_p50 = 0.0
    total_deviation_p100 = 0.0
    valid_samples = 0

    print(f"\n{'Time':>6} | {'Total':>6} | {'Cmd %':^24} | {'Act %':^24} | {'Deviation (pp)':^24}")
    print(f"{'(s)':>6} | {'Reqs':>6} | {'p30':>6} {'p50':>6} {'p100':>6} | {'p30':>6} {'p50':>6} {'p100':>6} | {'p30':>6} {'p50':>6} {'p100':>6}")
    print("-" * 80)

    for row in rows:
        try:
            elapsed = float(row['elapsed_seconds'])

            # Get actual request counts
            req_p30 = int(row['requests_precision_30'])
            req_p50 = int(row['requests_precision_50'])
            req_p100 = int(row['requests_precision_100'])
            total_reqs = req_p30 + req_p50 + req_p100

            if total_reqs == 0:
                continue

            # Calculate actual percentages
            act_p30 = 100.0 * req_p30 / total_reqs
            act_p50 = 100.0 * req_p50 / total_reqs
            act_p100 = 100.0 * req_p100 / total_reqs

            # Get commanded weights (if available)
            cmd_p30_str = row['commanded_weight_30'].strip()
            cmd_p50_str = row['commanded_weight_50'].strip()
            cmd_p100_str = row['commanded_weight_100'].strip()

            if not cmd_p30_str or not cmd_p50_str or not cmd_p100_str:
                continue

            cmd_p30 = float(cmd_p30_str)
            cmd_p50 = float(cmd_p50_str)
            cmd_p100 = float(cmd_p100_str)

            # Calculate deviations
            dev_p30 = act_p30 - cmd_p30
            dev_p50 = act_p50 - cmd_p50
            dev_p100 = act_p100 - cmd_p100

            total_deviation_p30 += dev_p30
            total_deviation_p50 += dev_p50
            total_deviation_p100 += dev_p100
            valid_samples += 1

            print(f"{elapsed:6.1f} | {total_reqs:6} | "
                  f"{cmd_p30:6.1f} {cmd_p50:6.1f} {cmd_p100:6.1f} | "
                  f"{act_p30:6.1f} {act_p50:6.1f} {act_p100:6.1f} | "
                  f"{dev_p30:+6.1f} {dev_p50:+6.1f} {dev_p100:+6.1f}")

        except (ValueError, KeyError) as e:
            continue

    print("-" * 80)

    if valid_samples > 0:
        avg_dev_p30 = total_deviation_p30 / valid_samples
        avg_dev_p50 = total_deviation_p50 / valid_samples
        avg_dev_p100 = total_deviation_p100 / valid_samples

        print(f"\nAverage deviation across {valid_samples} samples:")
        print(f"  p30:  {avg_dev_p30:+.2f} pp")
        print(f"  p50:  {avg_dev_p50:+.2f} pp")
        print(f"  p100: {avg_dev_p100:+.2f} pp")

        # Statistical significance check
        print(f"\nExpected random deviation (1σ) for ~300 requests: ±2.5 pp")
        print(f"Observed p100 deviation: {avg_dev_p100:+.2f} pp")
        if abs(avg_dev_p100) > 2.5:
            sigma_count = abs(avg_dev_p100) / 2.5
            print(f"This is {sigma_count:.1f}σ - SYSTEMATIC bias (not random chance)")
        else:
            print("This is within expected random variance")
    else:
        print("\nNo valid samples found")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_weight_mismatch.py <path_to_timeseries.csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"Error: File not found: {csv_path}")
        sys.exit(1)

    analyze_mismatch(csv_path)
