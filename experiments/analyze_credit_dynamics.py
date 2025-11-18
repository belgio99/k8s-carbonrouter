#!/usr/bin/env python3
"""Analyze credit balance dynamics to understand oscillation frequency."""

import csv
from pathlib import Path

csv_path = Path("results/simple_20251118_162323/forecast-aware-global/timeseries.csv")

print("=" * 100)
print("CREDIT BALANCE DYNAMICS ANALYSIS")
print("=" * 100)
print()

balances = []
timestamps = []
request_counts = []

with open(csv_path) as f:
    reader = csv.DictReader(f)
    for row in reader:
        time = float(row['elapsed_seconds'])
        balance = float(row['credit_balance'])
        total_req = (int(row['requests_precision_30']) +
                    int(row['requests_precision_50']) +
                    int(row['requests_precision_100']))

        timestamps.append(time)
        balances.append(balance)
        request_counts.append(total_req)

if len(balances) < 2:
    print("❌ Not enough data")
    exit(1)

# Credit statistics
min_credit = min(balances)
max_credit = max(balances)
avg_credit = sum(balances) / len(balances)
credit_range = max_credit - min_credit

print(f"📊 Credit Balance Range:")
print(f"   Min: {min_credit:+.2f}")
print(f"   Max: {max_credit:+.2f}")
print(f"   Avg: {avg_credit:+.2f}")
print(f"   Range: {credit_range:.2f}")
print()

# Oscillation frequency
crossings = 0
for i in range(1, len(balances)):
    # Count zero crossings
    if (balances[i-1] < 0 and balances[i] >= 0) or (balances[i-1] >= 0 and balances[i] < 0):
        crossings += 1

total_time = timestamps[-1] - timestamps[0]
avg_period = total_time / crossings if crossings > 0 else float('inf')

print(f"🔄 Oscillation Frequency:")
print(f"   Zero crossings: {crossings}")
print(f"   Average period: {avg_period:.1f} seconds")
print()

# Credit velocity
changes = [abs(balances[i] - balances[i-1]) for i in range(1, len(balances))]
avg_change = sum(changes) / len(changes)
max_change = max(changes)

print(f"⚡ Credit Velocity:")
print(f"   Average |change|: {avg_change:.3f} per sample")
print(f"   Max |change|: {max_change:.3f} per sample")
print()

# Request rate
total_requests = request_counts[-1]
rps = total_requests / total_time

print(f"🚦 Request Rate:")
print(f"   Total requests: {total_requests}")
print(f"   Duration: {total_time:.1f} seconds")
print(f"   Average RPS: {rps:.2f}")
print()

# Credit impact per request
credit_per_request = credit_range / total_requests if total_requests > 0 else 0

print(f"💡 Credit Impact:")
print(f"   Full range oscillation requires: {total_requests} requests")
print(f"   Credit change per request: {credit_per_request:.4f}")
print()

print("=" * 100)
print("ASSESSMENT:")
print("=" * 100)
print()

if avg_period < 60:
    print("⚠️  Credit oscillates VERY RAPIDLY (< 1 minute cycles)")
    print()
    print("Recommendations:")
    print("  1. REDUCE request rate by lowering user count in benchmark")
    print("  2. OR widen credit capacity (increase credit_max - credit_min range)")
    print()
    print(f"Current rate: {rps:.2f} RPS")
    print(f"Suggested: Reduce to ~{rps * 0.5:.2f} RPS (50% reduction)")
    print(f"   → Change users from current to ~{int(10 * 0.5)} in run_simple_benchmark.py")
elif avg_period < 120:
    print("⚠️  Credit oscillates RAPIDLY (1-2 minute cycles)")
    print()
    print("This is borderline acceptable. Consider:")
    print("  - Slightly reducing request rate (10-20%)")
    print("  - OR slightly widening credit capacity")
else:
    print("✅ Credit oscillation period is ACCEPTABLE (>2 minutes)")
    print()
    print("The 'tank size' is appropriate for this workload.")
