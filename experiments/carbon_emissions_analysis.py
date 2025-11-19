#!/usr/bin/env python3
"""
Calculate ACTUAL carbon emissions for strategies.
Measures gCO2 emitted per request served, weighted by carbon intensity at time of service.
"""

import csv
from pathlib import Path

def calculate_emissions(run_path):
    """Calculate carbon emissions weighted by actual grid intensity."""
    full_path = Path("results") / run_path / "timeseries.csv"

    total_emissions = 0.0
    total_requests = 0
    total_energy = 0.0

    with open(full_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            carbon_now = float(row['carbon_now'])

            # Requests served at this timestamp
            p30_reqs = float(row['requests_precision_30'])
            p50_reqs = float(row['requests_precision_50'])
            p100_reqs = float(row['requests_precision_100'])

            # Energy consumed by each flavour (relative units)
            # p30 uses 30% CPU, p50 uses 60%, p100 uses 100%
            energy_p30 = p30_reqs * 0.3
            energy_p50 = p50_reqs * 0.6
            energy_p100 = p100_reqs * 1.0

            sample_energy = energy_p30 + energy_p50 + energy_p100
            sample_emissions = sample_energy * carbon_now  # gCO2 = kWh × gCO2/kWh

            total_energy += sample_energy
            total_emissions += sample_emissions
            total_requests += (p30_reqs + p50_reqs + p100_reqs)

    return {
        'total_requests': int(total_requests),
        'total_energy': total_energy,
        'total_emissions': total_emissions,
        'emissions_per_request': total_emissions / total_requests if total_requests > 0 else 0,
        'energy_per_request': total_energy / total_requests if total_requests > 0 else 0,
    }


def compare_strategies():
    """Compare carbon emissions across strategies."""
    strategies = [
        ("simple_20251119_180013/forecast-aware-global", "forecast-aware-global"),
        ("simple_20251119_190241/credit-greedy", "credit-greedy"),
    ]

    print("="*80)
    print("CARBON EMISSIONS ANALYSIS")
    print("="*80)
    print()

    results = {}
    for path, name in strategies:
        full_path = Path("results") / path / "timeseries.csv"
        if not full_path.exists():
            print(f"❌ {name}: File not found")
            continue

        stats = calculate_emissions(path)
        results[name] = stats

        print(f"{name.upper()}")
        print(f"  Total requests:         {stats['total_requests']:,}")
        print(f"  Total energy consumed:  {stats['total_energy']:.1f} (relative units)")
        print(f"  Total emissions:        {stats['total_emissions']:.1f} gCO2")
        print(f"  Energy per request:     {stats['energy_per_request']:.4f}")
        print(f"  Emissions per request:  {stats['emissions_per_request']:.4f} gCO2/req")
        print()

    # Comparison
    if len(results) == 2:
        fg = results['forecast-aware-global']
        cg = results['credit-greedy']

        print("="*80)
        print("COMPARISON")
        print("="*80)
        print()

        energy_diff = fg['energy_per_request'] - cg['energy_per_request']
        energy_pct = (energy_diff / cg['energy_per_request']) * 100

        emissions_diff = fg['emissions_per_request'] - cg['emissions_per_request']
        emissions_pct = (emissions_diff / cg['emissions_per_request']) * 100

        print(f"Energy per request:")
        print(f"  credit-greedy:         {cg['energy_per_request']:.4f}")
        print(f"  forecast-aware-global: {fg['energy_per_request']:.4f}")
        print(f"  Difference:            {energy_diff:+.4f} ({energy_pct:+.2f}%)")
        print()

        print(f"Emissions per request:")
        print(f"  credit-greedy:         {cg['emissions_per_request']:.4f} gCO2/req")
        print(f"  forecast-aware-global: {fg['emissions_per_request']:.4f} gCO2/req")
        print(f"  Difference:            {emissions_diff:+.4f} gCO2/req ({emissions_pct:+.2f}%)")
        print()

        print("INTERPRETATION:")
        if emissions_diff < 0:
            print(f"  ✅ forecast-aware-global emits {abs(emissions_pct):.1f}% LESS carbon per request")
        else:
            print(f"  ❌ forecast-aware-global emits {emissions_pct:.1f}% MORE carbon per request")

        if energy_diff < 0:
            print(f"  ✅ forecast-aware-global uses {abs(energy_pct):.1f}% LESS energy per request")
        else:
            print(f"  ⚠️  forecast-aware-global uses {energy_pct:.1f}% MORE energy per request")
        print()

        # Total emissions comparison (for reference, but not apples-to-apples due to duration)
        print("Total emissions (reference only - different test durations):")
        print(f"  credit-greedy:         {cg['total_emissions']:.1f} gCO2 total")
        print(f"  forecast-aware-global: {fg['total_emissions']:.1f} gCO2 total")
        print()


if __name__ == "__main__":
    compare_strategies()
