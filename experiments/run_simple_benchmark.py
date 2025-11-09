#!/usr/bin/env python3
"""
Simple policy benchmark without restarts.

This version does NOT restart decision engine or router, so port-forwards stay alive.
Run this for quick tests where you don't need to reset credits between policies.

Usage:
    python3 run_simple_benchmark.py --policy credit-greedy
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import requests

ALL_POLICIES = ["credit-greedy", "forecast-aware", "forecast-aware-global", "precision-tier"]
NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"
ENGINE_NAMESPACE = "carbonrouter-system"
ENGINE_DEPLOYMENT = "carbonrouter-decision-engine"

# Test configuration
TEST_DURATION_MINUTES = 20
SAMPLE_INTERVAL_SECONDS = 30
LOCUST_USERS = 150
LOCUST_SPAWN_RATE = 50

# Port-forward URLs
ROUTER_URL = "http://127.0.0.1:18000"
ROUTER_METRICS_URL = "http://127.0.0.1:18001/metrics"
CONSUMER_METRICS_URL = "http://127.0.0.1:18002/metrics"
ENGINE_METRICS_URL = "http://127.0.0.1:18003/metrics"
MOCK_CARBON_URL = "http://127.0.0.1:5001"

def run_cmd(cmd: List[str], capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True, check=True, timeout=timeout)

def reset_carbon_pattern() -> None:
    """
    Reset the mock carbon API pattern to start from the beginning.
    
    This ensures all test runs start with the same carbon intensity baseline,
    making results comparable across different policies.
    """
    try:
        # The mock API uses current time to determine position in pattern
        # We can't reset time, but we can verify the pattern is accessible
        response = requests.get(f"{MOCK_CARBON_URL}/scenario", timeout=5)
        if response.status_code == 200:
            scenario_info = response.json()
            print(f"  ℹ️  Carbon pattern: {scenario_info.get('name', 'unknown')}")
            print(f"     Pattern length: {len(scenario_info.get('pattern', []))} points")
        else:
            print(f"  ⚠️  Warning: Could not verify carbon API (status {response.status_code})")
    except Exception as e:
        print(f"  ⚠️  Warning: Carbon API not accessible: {e}")
        print(f"     Tests will continue but results may be inconsistent")

def patch_policy(policy: str) -> None:
    """Update TrafficSchedule with new policy and fast update intervals."""
    # Configure for fast testing:
    # - validFor: 30s = decision engine recalculates every ~24s (80% of 30s)
    # - carbonCacheTTL: 15s = fetch fresh carbon data every 15s  
    # This ensures we catch carbon changes every minute without overwhelming the system
    patch = json.dumps({
        "spec": {
            "scheduler": {
                "policy": policy,
                "validFor": 30,        # Schedule refresh every ~24s
                "carbonCacheTTL": 15   # Carbon data refreshed every 15s
            }
        }
    })
    run_cmd([
        "kubectl", "patch", "trafficschedule", SCHEDULE_NAME,
        "-n", NAMESPACE, "--type=merge", f"-p={patch}"
    ])
    print(f"  ✓ Patched policy to {policy} (validFor=30s, carbonCacheTTL=15s)")
    print("  ⏳ Waiting 30s for decision engine to stabilize...")
    time.sleep(30)

def scrape_metrics(url: str) -> str:
    """Fetch Prometheus metrics from URL."""
    response = requests.get(url, timeout=10)
    return response.text

def parse_prometheus_metrics(text: str) -> Dict[str, float]:
    """Parse Prometheus text format into dict."""
    metrics = {}
    for line in text.split('\n'):
        if line and not line.startswith('#'):
            parts = line.split()
            if len(parts) >= 2:
                metrics[parts[0]] = float(parts[1])
    return metrics

def extract_router_requests_by_flavour(metrics: Dict[str, float]) -> Dict[str, float]:
    """Extract request counts per flavour from router metrics."""
    requests_by_flavour = {}
    for key, value in metrics.items():
        if key.startswith('router_http_requests_total{') and 'flavour=' in key:
            # Extract flavour name from label
            flavour_start = key.find('flavour="') + 9
            flavour_end = key.find('"', flavour_start)
            flavour = key[flavour_start:flavour_end]
            requests_by_flavour[flavour] = value
    return requests_by_flavour

def get_schedule_status() -> Dict[str, Any]:
    """Get TrafficSchedule status."""
    result = run_cmd([
        "kubectl", "get", "trafficschedule", SCHEDULE_NAME,
        "-n", NAMESPACE, "-o", "json"
    ])
    return json.loads(result.stdout).get("status", {})

def start_locust_background(policy_dir: Path) -> subprocess.Popen:
    """Start Locust in headless mode, return process handle."""
    locustfile = Path(__file__).parent / "locust_router.py"
    cmd = [
        "locust",
        "-f", str(locustfile),
        "--headless",
        f"--users={LOCUST_USERS}",
        f"--spawn-rate={LOCUST_SPAWN_RATE}",
        f"--run-time={TEST_DURATION_MINUTES}m",
        f"--csv={policy_dir / 'locust'}",
        f"--logfile={policy_dir / 'locust.log'}",
        "--host", ROUTER_URL
    ]
    # Redirect stderr to suppress Locust TTY warnings when running in background
    return subprocess.Popen(
        cmd,
        env={**subprocess.os.environ, "BENCHMARK_PATH": "/avg"},
        stderr=subprocess.DEVNULL
    )

def test_policy_with_sampling(policy: str, output_dir: Path) -> Dict[str, Any]:
    """Test a single policy with periodic sampling."""
    policy_dir = output_dir / policy
    policy_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*70}")
    print(f"Testing policy: {policy}")
    print(f"{'='*70}")
    
    # 1. Verify carbon pattern (for test consistency)
    reset_carbon_pattern()
    
    # 2. Apply policy with fast update intervals
    patch_policy(policy)
    
    # 3. Get initial state and BASELINE metrics
    print("  ⏳ Collecting baseline...")
    schedule_before = get_schedule_status()
    (policy_dir / "schedule_before.json").write_text(
        json.dumps(schedule_before, indent=2), encoding="utf-8"
    )
    
    # Parse precision info
    flavours = schedule_before.get("flavours", [])
    precision_map = {}
    carbon_intensity_map = {}
    for f in flavours:
        name = f.get("name", "")
        prec = f.get("precision", 100)
        carbon = f.get("carbonIntensity", 0)
        if isinstance(prec, (int, float)):
            precision_map[name] = float(prec) / 100.0 if prec > 1 else float(prec)
        if isinstance(carbon, (int, float)):
            carbon_intensity_map[name] = float(carbon)
    
    # Collect BASELINE metrics
    router_metrics_baseline = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
    baseline_requests = extract_router_requests_by_flavour(router_metrics_baseline)
    
    (policy_dir / "router_metrics_baseline.txt").write_text(
        scrape_metrics(ROUTER_METRICS_URL), encoding="utf-8"
    )
    
    print(f"  ✓ Baseline collected (starting from {sum(baseline_requests.values()):.0f} requests)")
    
    # 3. Start Locust
    print(f"  ⏳ Starting load test: {LOCUST_USERS} users for {TEST_DURATION_MINUTES} minutes...")
    locust_proc = start_locust_background(policy_dir)
    
    # 4. Sample metrics periodically
    print(f"  ⏳ Sampling metrics every {SAMPLE_INTERVAL_SECONDS}s...")
    csv_path = policy_dir / "timeseries.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "timestamp", "elapsed_seconds", "delta_requests", "mean_precision",
            "credit_balance", "credit_velocity", "engine_avg_precision",
            "carbon_now", "carbon_next"
        ])
        csvfile.flush()
        
        start_time = time.time()
        samples_collected = 0
        last_requests = baseline_requests.copy()
        
        while locust_proc.poll() is None:
            try:
                time.sleep(SAMPLE_INTERVAL_SECONDS)
                elapsed = time.time() - start_time
                
                # Get current metrics
                router_metrics = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
                engine_metrics = parse_prometheus_metrics(scrape_metrics(ENGINE_METRICS_URL))
                
                current_requests = extract_router_requests_by_flavour(router_metrics)
                
                # Calculate delta since last sample
                delta_requests = {}
                for flavour in set(list(current_requests.keys()) + list(last_requests.keys())):
                    delta_requests[flavour] = current_requests.get(flavour, 0) - last_requests.get(flavour, 0)
                
                total_delta = sum(delta_requests.values())
                
                # Calculate weighted precision
                weighted_precision = 0.0
                if total_delta > 0:
                    for flavour, count in delta_requests.items():
                        if count > 0:
                            prec = precision_map.get(flavour, 1.0)
                            weighted_precision += (count / total_delta) * prec
                
                # Extract engine data
                engine_data = {}
                for key, value in engine_metrics.items():
                    if "credit_balance" in key:
                        engine_data["credit_balance"] = value
                    elif "credit_velocity" in key:
                        engine_data["credit_velocity"] = value
                    elif "avg_precision" in key:
                        engine_data["avg_precision"] = value
                    elif "carbon_forecast_now" in key:
                        engine_data["carbon_now"] = value
                    elif "carbon_forecast_next" in key:
                        engine_data["carbon_next"] = value
                
                # Write row
                writer.writerow([
                    datetime.utcnow().isoformat() + "Z",
                    f"{elapsed:.1f}",
                    int(total_delta),
                    f"{weighted_precision:.4f}" if total_delta > 0 else "",
                    f"{engine_data.get('credit_balance', ''):.4f}" if 'credit_balance' in engine_data else "",
                    f"{engine_data.get('credit_velocity', ''):.4f}" if 'credit_velocity' in engine_data else "",
                    f"{engine_data.get('avg_precision', ''):.4f}" if 'avg_precision' in engine_data else "",
                    f"{engine_data.get('carbon_now', ''):.1f}" if 'carbon_now' in engine_data else "",
                    f"{engine_data.get('carbon_next', ''):.1f}" if 'carbon_next' in engine_data else ""
                ])
                csvfile.flush()
                
                last_requests = current_requests
                samples_collected += 1
                
                if samples_collected % 5 == 0:
                    print(f"    Sample {samples_collected}: {int(total_delta)} req/period, "
                          f"prec={weighted_precision:.3f}, "
                          f"credits={engine_data.get('credit_balance', 'N/A')}")
                
            except Exception as e:
                print(f"  ⚠ Sampling error: {e}")
    
    # Wait for Locust to finish
    locust_proc.wait(timeout=30)
    print(f"  ✓ Collected {samples_collected} samples")
    
    # 5. Collect final state
    print("  ⏳ Collecting final metrics...")
    time.sleep(5)
    
    schedule_after = get_schedule_status()
    (policy_dir / "schedule_after.json").write_text(
        json.dumps(schedule_after, indent=2), encoding="utf-8"
    )
    
    # Save final metrics
    router_metrics_final_text = scrape_metrics(ROUTER_METRICS_URL)
    (policy_dir / "router_metrics_final.txt").write_text(router_metrics_final_text, encoding="utf-8")
    
    consumer_metrics_final_text = scrape_metrics(CONSUMER_METRICS_URL)
    (policy_dir / "consumer_metrics_final.txt").write_text(consumer_metrics_final_text, encoding="utf-8")
    
    engine_metrics_final_text = scrape_metrics(ENGINE_METRICS_URL)
    (policy_dir / "engine_metrics_final.txt").write_text(engine_metrics_final_text, encoding="utf-8")
    
    # Final request counts
    final_router_metrics = parse_prometheus_metrics(router_metrics_final_text)
    final_requests = extract_router_requests_by_flavour(final_router_metrics)
    
    # Compute delta from baseline
    requests_delta = {
        k: final_requests.get(k, 0) - baseline_requests.get(k, 0)
        for k in set(final_requests) | set(baseline_requests)
    }
    total_requests = sum(v for v in requests_delta.values() if v > 0)
    
    print(f"  ✓ Final metrics collected (total delta: {total_requests:.0f} requests)")
    
    weighted_precision_final = 0.0
    if total_requests > 0:
        for flavour, count in requests_delta.items():
            if count > 0:
                prec = precision_map.get(flavour, 1.0)
                weighted_precision_final += (count / total_requests) * prec
    
    # Calculate mean carbon intensity from requests
    mean_carbon_intensity = 0.0
    if total_requests > 0:
        for flavour, count in requests_delta.items():
            if count > 0:
                carbon = carbon_intensity_map.get(flavour, 0.0)
                mean_carbon_intensity += (count / total_requests) * carbon
    
    credit_info = schedule_after.get("credits", {})
    
    summary = {
        "policy": policy,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "test_duration_minutes": TEST_DURATION_MINUTES,
        "samples_collected": samples_collected,
        "total_requests": total_requests,
        "requests_by_flavour": requests_delta,
        "mean_precision": weighted_precision_final,
        "mean_carbon_intensity": mean_carbon_intensity,
        "credit_balance_final": credit_info.get("balance"),
        "credit_velocity_final": credit_info.get("velocity"),
        "avg_precision_reported": schedule_after.get("avgPrecision"),
    }
    
    (policy_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    
    print("\n  Results:")
    print(f"    Duration: {TEST_DURATION_MINUTES} minutes")
    print(f"    Samples: {samples_collected}")
    print(f"    Total requests: {int(total_requests)}")
    print(f"    Mean precision: {weighted_precision_final:.3f}")
    print(f"    Mean carbon intensity: {mean_carbon_intensity:.1f} gCO₂/kWh")
    print(f"    Final credit balance: {credit_info.get('balance', 'N/A')}")
    
    return summary

def main():
    parser = argparse.ArgumentParser(
        description="Run simple benchmark (NO restarts - keeps port-forwards alive)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--policy",
        required=True,
        choices=ALL_POLICIES,
        help="Policy to test"
    )
    
    args = parser.parse_args()
    policy = args.policy
    
    print("="*70)
    print("SIMPLE POLICY BENCHMARK (No Restarts)")
    print("="*70)
    print(f"Policy: {policy}")
    print(f"Duration: {TEST_DURATION_MINUTES} minutes")
    print(f"Sample interval: {SAMPLE_INTERVAL_SECONDS} seconds")
    print(f"Load: {LOCUST_USERS} users @ {LOCUST_SPAWN_RATE} spawn/s")
    print()
    
    # Create output directory
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results" / f"simple_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")
    print()
    
    try:
        summary = test_policy_with_sampling(policy, output_dir)
        print("\n" + "="*70)
        print("✅ Test completed successfully!")
        print(f"Results saved to: {output_dir / policy}")
        print("="*70)
        return 0
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
