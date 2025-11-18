#!/usr/bin/env python3
"""Quick test to verify credit balance sign fix."""

import sys
sys.path.insert(0, '/Users/belgio/git-repos/k8s-carbonaware-scheduler/decision-engine')

from scheduler.ledger import CreditLedger

# Create a ledger with target_error = 0.1 (10%)
ledger = CreditLedger(
    target_error=0.1,
    credit_min=-1.0,
    credit_max=1.0,
    window_size=10
)

print("Testing Credit Balance Sign Convention Fix")
print("=" * 60)
print(f"Initial balance: {ledger.balance:.4f}")
print(f"Target error: {ledger.target_error}")
print()

print("Test 1: Using HIGH precision (0.95) - should INCREASE balance")
print("-" * 60)
for i in range(5):
    balance = ledger.update(0.95)
    print(f"  Request {i+1}: precision=0.95, balance={balance:.4f}")

print()
print("Test 2: Using LOW precision (0.30) - should DECREASE balance")
print("-" * 60)
for i in range(5):
    balance = ledger.update(0.30)
    print(f"  Request {i+1}: precision=0.30, balance={balance:.4f}")

print()
print("Test 3: Return to HIGH precision (0.95) - should INCREASE balance")
print("-" * 60)
for i in range(3):
    balance = ledger.update(0.95)
    print(f"  Request {i+1}: precision=0.95, balance={balance:.4f}")

print()
print("=" * 60)
final_velocity = ledger.velocity()
print(f"Final balance: {ledger.balance:.4f}")
print(f"Final velocity: {final_velocity:.4f}")
print()

if ledger.balance > 0:
    print("✓ PASS: Balance is POSITIVE (surplus from recent high-precision use)")
else:
    print("✗ FAIL: Balance is NEGATIVE (should be positive after high-precision use)")

print()
print("Explanation:")
print("  With the FIXED sign convention:")
print("  - Positive balance = quality SURPLUS (earned from high-precision use)")
print("  - Negative balance = quality DEBT (from low-precision use)")
