#!/usr/bin/env python3
"""Analyze temporal benchmark results and generate summary statistics."""

import json
import csv
from pathlib import Path
from typing import Dict, List, Any
import sys

def analyze_timeseries(csv_path: Path) -> Dict[str, Any]:
    """Analyze a timeseries CSV file."""
    if not csv_path.exists():
        return {}
    
    samples = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                samples.append({
                    'timestamp': row['timestamp'],
                    'elapsed': float(row['elapsed_seconds']),
                    'requests': int(row['delta_requests']) if row['delta_requests'] else 0,
                    'precision': float(row['mean_precision']) if row['mean_precision'] else None,
                    'credit_balance': float(row['credit_balance']) if row['credit_balance'] else None,
                    'carbon_now': float(row['carbon_now']) if row['carbon_now'] else None,
                })
            except (ValueError, KeyError):
                continue
    
    if not samples:
        return {}
    
    # Calculate statistics
    precisions = [s['precision'] for s in samples if s['precision'] is not None]
    credits = [s['credit_balance'] for s in samples if s['credit_balance'] is not None]
    carbon_values = [s['carbon_now'] for s in samples if s['carbon_now'] is not None]
    total_requests = sum(s['requests'] for s in samples)
    
    return {
        'num_samples': len(samples),
        'total_requests': total_requests,
        'mean_precision': sum(precisions) / len(precisions) if precisions else 0,
        'min_precision': min(precisions) if precisions else 0,
        'max_precision': max(precisions) if precisions else 0,
        'mean_credit': sum(credits) / len(credits) if credits else 0,
        'min_credit': min(credits) if credits else 0,
        'max_credit': max(credits) if credits else 0,
        'final_credit': credits[-1] if credits else None,
        'mean_carbon': sum(carbon_values) / len(carbon_values) if carbon_values else 0,
        'min_carbon': min(carbon_values) if carbon_values else 0,
        'max_carbon': max(carbon_values) if carbon_values else 0,
        'carbon_range': (min(carbon_values), max(carbon_values)) if carbon_values else (0, 0),
    }

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_results.py <results_directory>")
        sys.exit(1)
    
    results_dir = Path(sys.argv[1])
    if not results_dir.exists():
        print(f"Error: {results_dir} does not exist")
        sys.exit(1)
    
    print("="*80)
    print("TEMPORAL BENCHMARK ANALYSIS")
    print("="*80)
    print(f"Results directory: {results_dir}")
    print()
    
    # Load benchmark summary
    summary_file = results_dir / "benchmark_summary.json"
    if summary_file.exists():
        with open(summary_file, 'r', encoding='utf-8') as f:
            summaries = json.load(f)
    else:
        summaries = []
    
    # Analyze each policy
    analyses = []
    for policy_dir in sorted(results_dir.iterdir()):
        if not policy_dir.is_dir():
            continue
        
        policy_name = policy_dir.name
        timeseries_path = policy_dir / "timeseries.csv"
        
        if not timeseries_path.exists():
            print(f"⚠ No timeseries data for {policy_name}")
            continue
        
        analysis = analyze_timeseries(timeseries_path)
        analysis['policy'] = policy_name
        analyses.append(analysis)
        
        print(f"\n{policy_name}")
        print("─" * 80)
        print(f"  Samples collected: {analysis['num_samples']}")
        print(f"  Total requests: {analysis['total_requests']:,}")
        print(f"  Precision: {analysis['mean_precision']:.4f} "
              f"(range: {analysis['min_precision']:.4f} - {analysis['max_precision']:.4f})")
        print(f"  Credits: {analysis['mean_credit']:.4f} "
              f"(range: {analysis['min_credit']:.4f} - {analysis['max_credit']:.4f})")
        print(f"  Final credit balance: {analysis['final_credit']:.4f}" if analysis['final_credit'] else "  Final credit balance: N/A")
        print(f"  Carbon intensity: {analysis['mean_carbon']:.1f} gCO₂/kWh "
              f"(range: {analysis['min_carbon']:.1f} - {analysis['max_carbon']:.1f})")
    
    # Comparison table
    print("\n" + "="*80)
    print("POLICY COMPARISON")
    print("="*80)
    print(f"\n{'Policy':<28} {'Requests':<10} {'Mean Prec':<12} {'Prec StdDev':<12} {'Final Credits':<14}")
    print("─" * 80)
    
    for analysis in analyses:
        policy = analysis['policy']
        reqs = analysis['total_requests']
        mean_prec = analysis['mean_precision']
        prec_range = analysis['max_precision'] - analysis['min_precision']
        final_credit = analysis['final_credit']
        
        credit_str = f"{final_credit:.4f}" if final_credit is not None else "N/A"
        print(f"{policy:<28} {reqs:<10,} {mean_prec:<12.4f} {prec_range:<12.4f} {credit_str:<14}")
    
    # Save detailed analysis
    analysis_file = results_dir / "detailed_analysis.json"
    with open(analysis_file, 'w', encoding='utf-8') as f:
        json.dump(analyses, f, indent=2)
    
    print(f"\n{'='*80}")
    print(f"Detailed analysis saved to: {analysis_file}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
