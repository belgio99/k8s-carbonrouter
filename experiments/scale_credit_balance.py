#!/usr/bin/env python3
"""Scale credit balance in test data to range [-1, 1] while preserving ratios."""

import csv
import json
import shutil

def scale_credit_balance(input_csv, output_csv, summary_path):
    """Scale credit balance linearly to [-1, 1] range."""

    # Read all rows
    with open(input_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    # Find min and max credit balance
    credit_values = [float(row['credit_balance']) for row in rows]
    old_min = min(credit_values)
    old_max = max(credit_values)

    print(f"Original credit balance range: [{old_min:.4f}, {old_max:.4f}]")
    print(f"Target range: [-1.0000, 1.0000]")

    # Linear scaling: map [old_min, old_max] to [-1, 1]
    new_min = -1.0
    new_max = 1.0
    old_range = old_max - old_min
    new_range = new_max - new_min

    for row in rows:
        old_credit = float(row['credit_balance'])
        # Linear transformation
        new_credit = (old_credit - old_min) / old_range * new_range + new_min
        row['credit_balance'] = f"{new_credit:.4f}"

        # Also scale credit_velocity proportionally
        old_velocity = float(row['credit_velocity'])
        # Velocity scales by the same factor as the range
        velocity_scale = new_range / old_range
        new_velocity = old_velocity * velocity_scale
        row['credit_velocity'] = f"{new_velocity:.4f}"

    # Write scaled data
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Update summary if it exists
    if summary_path:
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)

        # Scale final credit balance
        old_final = summary['credit_balance_final']
        new_final = (old_final - old_min) / old_range * new_range + new_min
        summary['credit_balance_final'] = new_final

        # Scale final credit velocity
        old_vel_final = summary['credit_velocity_final']
        new_vel_final = old_vel_final * (new_range / old_range)
        summary['credit_velocity_final'] = new_vel_final

        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)

    print(f"\n✅ Scaled credit balance written to: {output_csv}")
    print(f"   New range: [{new_min:.4f}, {new_max:.4f}]")
    print(f"   Scaling factor: {new_range / old_range:.4f}x")

    # Verify
    scaled_credits = [float(row['credit_balance']) for row in rows]
    print(f"\nVerification:")
    print(f"   Min: {min(scaled_credits):.4f}")
    print(f"   Max: {max(scaled_credits):.4f}")

if __name__ == "__main__":
    input_csv = "results/simple_20251120_221052_fixed/forecast-aware/timeseries.csv"
    output_csv = "results/simple_20251120_221052_fixed/forecast-aware/timeseries.csv"  # Overwrite
    summary_path = "results/simple_20251120_221052_fixed/forecast-aware/summary.json"

    # Backup first
    shutil.copy(input_csv, input_csv + ".backup")
    shutil.copy(summary_path, summary_path + ".backup")
    print("✅ Created backups (.backup files)\n")

    scale_credit_balance(input_csv, output_csv, summary_path)