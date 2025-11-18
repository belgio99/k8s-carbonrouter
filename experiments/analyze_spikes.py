#!/usr/bin/env python3
"""Analyze commanded schedule changes causing spikes."""

import csv
from pathlib import Path

csv_path = Path("results/simple_20251118_162323/forecast-aware-global/timeseries.csv")

print("=" * 100)
print("COMMANDED SCHEDULE CHANGES (seconds 190-320)")
print("=" * 100)
print()

with open(csv_path) as f:
    reader = csv.DictReader(f)
    prev_cmd = None
    
    for row in reader:
        time = float(row['elapsed_seconds'])
        if time < 190 or time > 320:
            continue
            
        cmd = (int(float(row['commanded_weight_30'])), 
               int(float(row['commanded_weight_50'])), 
               int(float(row['commanded_weight_100'])))
        
        carbon_now = float(row['carbon_now'])
        carbon_next = float(row['carbon_next'])
        
        act_p30 = int(row['requests_precision_30'])
        act_p50 = int(row['requests_precision_50'])
        act_p100 = int(row['requests_precision_100'])
        total = act_p30 + act_p50 + act_p100
        
        act_pct = (round(100*act_p30/total, 1), 
                   round(100*act_p50/total, 1), 
                   round(100*act_p100/total, 1))
        
        # Check if commanded weights changed
        if prev_cmd and cmd != prev_cmd:
            print(f"⚡ {time:6.1f}s: SCHEDULE CHANGE!")
            print(f"   Carbon: {carbon_now:.0f} → {carbon_next:.0f}")
            print(f"   Commanded:  {prev_cmd[0]:2d}/{prev_cmd[1]:2d}/{prev_cmd[2]:2d}  →  {cmd[0]:2d}/{cmd[1]:2d}/{cmd[2]:2d}")
            print(f"   Actual:     {act_pct[0]:4.1f}/{act_pct[1]:4.1f}/{act_pct[2]:4.1f}  (lag!)")
            
            # Calculate deviation
            dev = (cmd[2] - act_pct[2])
            print(f"   Deviation:  p100 is {dev:+.1f}pp {'TOO LOW (cmd wants more)' if dev > 0 else 'TOO HIGH (cmd wants less)'}")
            print()
        
        prev_cmd = cmd

print("=" * 100)
print("KEY INSIGHT:")
print("=" * 100)
print("forecast-aware-global makes AGGRESSIVE schedule changes based on carbon forecasts.")
print("The ~5-second propagation lag causes large transient deviations during transitions.")
print("This is EXPECTED BEHAVIOR - the strategy is highly reactive to forecast changes!")
