#!/usr/bin/env python3
"""Analyze credit ledger dynamics."""

import csv
from pathlib import Path

csv_path = Path("results/simple_20251118_162323/forecast-aware-global/timeseries.csv")

print("=" * 100)
print("CREDIT LEDGER DYNAMICS ANALYSIS")
print("=" * 100)
print()

with open(csv_path) as f:
    reader = csv.DictReader(f)
    
    prev_credit = None
    max_credit = -999
    min_credit = 999
    changes = []
    
    for row in reader:
        time = float(row['elapsed_seconds'])
        credit = float(row['credit_balance'])
        velocity = float(row['credit_velocity'])
        requests = int(row['delta_requests'])
        
        if prev_credit is not None:
            delta = credit - prev_credit
            changes.append(abs(delta))
        
        max_credit = max(max_credit, credit)
        min_credit = min(min_credit, credit)
        prev_credit = credit
        
        # Show some samples
        if 200 <= time <= 300 and int(time) % 10 == 0:
            print(f"{time:6.1f}s: credit={credit:+.3f}, velocity={velocity:+.4f}, Δreqs={requests:4d}")

print()
print("=" * 100)
print("STATISTICS:")
print("=" * 100)
print(f"Credit range: [{min_credit:+.3f}, {max_credit:+.3f}]  (span={max_credit-min_credit:.3f})")
print(f"Average absolute change per sample: {sum(changes)/len(changes):.4f}")
print(f"Max change per sample: {max(changes):.4f}")
print()

# Check request rate
with open(csv_path) as f:
    reader = csv.DictReader(f)
    total_reqs = sum(int(row['delta_requests']) for row in reader)
    
with open(csv_path) as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    duration = float(rows[-1]['elapsed_seconds'])

print(f"Total requests: {total_reqs}")
print(f"Duration: {duration:.1f}s")
print(f"Average RPS: {total_reqs/duration:.1f}")
print()

print("=" * 100)
print("ASSESSMENT:")
print("=" * 100)
print(f"With ~{total_reqs/duration:.0f} RPS and credit range of {max_credit-min_credit:.2f},")
print(f"each request affects credit by ~{(max_credit-min_credit)/(total_reqs/duration)/5:.6f} per request.")
print()
print("If credit oscillates too rapidly, consider:")
print("  1. Reduce request rate (lower user count in benchmark)")
print("  2. Increase credit capacity (widen min/max range)")
print("  3. Adjust credit velocity dampening")
