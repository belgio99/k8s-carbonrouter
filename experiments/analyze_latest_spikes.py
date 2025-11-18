#!/usr/bin/env python3
"""Analyze latest forecast-aware-global run for spikes."""

import csv
from pathlib import Path

csv_path = Path("results/simple_20251118_172950/forecast-aware-global/timeseries.csv")

print("=" * 100)
print("COMMANDED SCHEDULE CHANGES ANALYSIS")
print("=" * 100)
print()

changes = []
with open(csv_path) as f:
    reader = csv.DictReader(f)
    prev_cmd = None

    for row in reader:
        time = float(row['elapsed_seconds'])

        cmd = (int(float(row['commanded_weight_30'])),
               int(float(row['commanded_weight_50'])),
               int(float(row['commanded_weight_100'])))

        # Check if commanded weights changed
        if prev_cmd and cmd != prev_cmd:
            magnitude = abs(cmd[2] - prev_cmd[2])
            changes.append((time, prev_cmd, cmd, magnitude))

        prev_cmd = cmd

print(f"📊 Total schedule changes: {len(changes)}")
print()

# Statistics
if changes:
    mags = [c[3] for c in changes]
    avg_mag = sum(mags) / len(mags)
    max_mag = max(mags)

    large_changes = [c for c in changes if c[3] > 30]
    medium_changes = [c for c in changes if 15 < c[3] <= 30]
    small_changes = [c for c in changes if c[3] <= 15]

    print(f"Change Magnitude Statistics:")
    print(f"   Average: {avg_mag:.1f} pp")
    print(f"   Maximum: {max_mag} pp")
    print()
    print(f"Change Distribution:")
    print(f"   Large (>30pp):  {len(large_changes)} changes")
    print(f"   Medium (15-30pp): {len(medium_changes)} changes")
    print(f"   Small (≤15pp):  {len(small_changes)} changes")
    print()

    if large_changes:
        print(f"⚠️  LARGE CHANGES DETECTED:")
        for time, prev, curr, mag in large_changes[:10]:  # Show first 10
            print(f"   {time:6.1f}s: {prev[0]:2d}/{prev[1]:2d}/{prev[2]:2d} → {curr[0]:2d}/{curr[1]:2d}/{curr[2]:2d}  ({mag}pp)")
        print()
    else:
        print(f"✅ NO LARGE CHANGES (>30pp) - Strategy is smooth!")
        print()

    if medium_changes and len(medium_changes) <= 10:
        print(f"Medium changes (15-30pp):")
        for time, prev, curr, mag in medium_changes:
            print(f"   {time:6.1f}s: {prev[0]:2d}/{prev[1]:2d}/{prev[2]:2d} → {curr[0]:2d}/{curr[1]:2d}/{curr[2]:2d}  ({mag}pp)")
        print()

print("=" * 100)
print("ASSESSMENT:")
print("=" * 100)

if not large_changes:
    print("✅ EXCELLENT! No dramatic spikes (>30pp).")
    print("   Strategy transitions are smooth and gradual.")
elif len(large_changes) < 5:
    print("✓ GOOD! Very few large changes.")
    print(f"   Only {len(large_changes)} changes exceeded 30pp.")
else:
    print(f"⚠️  Still seeing significant oscillations: {len(large_changes)} large changes")
