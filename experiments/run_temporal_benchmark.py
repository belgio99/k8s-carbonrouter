#!/usr/bin/env python3
"""Enhanced policy benchmark with temporal carbon intensity changes.

This script tests each policy under realistic carbon intensity variations:
- 10 minute test duration per policy
- 1-minute time steps in the mock carbon API (180+ data points for 3h+ forecast)
- Periodic metric sampling every 30 seconds to capture adaptation behavior
- Decision engine restart between policies to reset credit state

The mock carbon API provides a simulated pattern that goes through:
- Rising intensity (morning simulation)
- Peak period
- Falling intensity (evening)
- Clean period (night)
- Volatile transitions

Results show how each policy adapts to changing conditions.

Usage:
    # Run all policies (40 minutes)
    python3 run_temporal_benchmark.py
    
    # Run specific policy only (10 minutes)
    python3 run_temporal_benchmark.py --policy credit-greedy
    python3 run_temporal_benchmark.py --policy forecast-aware
    python3 run_temporal_benchmark.py --policy forecast-aware-global
    python3 run_temporal_benchmark.py --policy precision-tier
    
    # Run multiple specific policies (20 minutes)
    python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware
"""

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
import sys
import csv
import requests

ALL_POLICIES = ["credit-greedy", "forecast-aware", "forecast-aware-global", "precision-tier"]
NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"
ENGINE_NAMESPACE = "carbonrouter-system"
ENGINE_DEPLOYMENT = "carbonrouter-decision-engine"

# Test configuration
TEST_DURATION_MINUTES = 10
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

def patch_policy(policy: str) -> None:
    """Update TrafficSchedule with new policy."""
    patch = json.dumps({"spec": {"scheduler": {"policy": policy}}})
    run_cmd([
        "kubectl", "patch", "trafficschedule", SCHEDULE_NAME,
        "-n", NAMESPACE, "--type=merge", f"-p={patch}"
    ])
    print(f"  ✓ Patched policy to {policy}")

def restart_decision_engine() -> None:
    """Restart decision engine to reset credit state."""
    run_cmd([
        "kubectl", "rollout", "restart", f"deployment/{ENGINE_DEPLOYMENT}",
        "-n", ENGINE_NAMESPACE
    ])
    print("  ✓ Restarted decision engine")
    
    # Wait for new pod to be ready
    print("  ⏳ Waiting for decision engine to be ready...")
    run_cmd([
        "kubectl", "rollout", "status", f"deployment/{ENGINE_DEPLOYMENT}",
        "-n", ENGINE_NAMESPACE, "--timeout=90s"
    ], timeout=120)
    print("  ✓ Decision engine ready")
    time.sleep(10)  # Extra settle time
    
    # Re-establish port-forward for decision engine metrics
    print("  ⏳ Re-establishing port-forward for decision engine...")
    subprocess.Popen(
        ["kubectl", "port-forward", f"-n={ENGINE_NAMESPACE}", 
         f"deployment/{ENGINE_DEPLOYMENT}", "18003:8001"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)
    print("  ✓ Port-forward ready")

def restart_router() -> None:
    """Restart router to reset metrics."""
    run_cmd([
        "kubectl", "rollout", "restart", f"deployment/buffer-service-router-{NAMESPACE}",
        "-n", NAMESPACE
    ])
    print("  ✓ Restarted router")
    
    # Wait for new pod to be ready
    print("  ⏳ Waiting for router to be ready...")
    run_cmd([
        "kubectl", "rollout", "status", f"deployment/buffer-service-router-{NAMESPACE}",
        "-n", NAMESPACE, "--timeout=60s"
    ], timeout=90)
    print("  ✓ Router ready")
    time.sleep(5)  # Extra settle time
    
    # Re-establish port-forwards for router
    print("  ⏳ Re-establishing port-forwards for router...")
    subprocess.Popen(
        ["kubectl", "port-forward", f"-n={NAMESPACE}", 
         f"service/buffer-service-router-{NAMESPACE}", "18000:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    subprocess.Popen(
        ["kubectl", "port-forward", f"-n={NAMESPACE}", 
         f"service/buffer-service-router-{NAMESPACE}", "18001:8001"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(5)
    
    # Verify port-forwards are working
    for i in range(10):
        try:
            requests.get("http://127.0.0.1:18001/metrics", timeout=2)
            print("  ✓ Port-forwards ready")
            break
        except Exception:
            if i == 9:
                print("  ⚠ Port-forwards may not be ready")
            time.sleep(1)

def reset_mock_carbon(scenario_file: Path) -> None:
    """Reset mock carbon API to initial scenario state."""
    try:
        import requests
        with open(scenario_file, 'r', encoding='utf-8') as f:
            scenario_data = json.load(f)
        
        payload = {
            "scenario": "custom",
            "pattern": scenario_data.get("pattern", [])
        }
        
        response = requests.post(f"{MOCK_CARBON_URL}/scenario", json=payload, timeout=10)
        response.raise_for_status()
        print(f"  ✓ Reset mock carbon API with {len(payload['pattern'])} data points")
    except Exception as e:
        print(f"  ⚠ Failed to reset mock carbon API: {e}")

def get_schedule_status() -> Dict[str, Any]:
    """Fetch current schedule status."""
    result = run_cmd([
        "kubectl", "get", "trafficschedule", SCHEDULE_NAME,
        "-n", NAMESPACE, "-o", "json"
    ])
    data = json.loads(result.stdout)
    return data.get("status", {})

def scrape_metrics(url: str) -> str:
    """Fetch Prometheus metrics from URL."""
    import requests
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text

def parse_prometheus_metrics(metrics_text: str) -> Dict[str, Any]:
    """Parse Prometheus text format into structured data."""
    result = {}
    
    for line in metrics_text.split('\n'):
        if line.startswith('#') or not line.strip():
            continue
        
        try:
            # Split metric name and value
            parts = line.split()
            if len(parts) < 2:
                continue
            
            metric_with_labels = parts[0]
            value = float(parts[1])
            
            # Extract metric name and labels
            if '{' in metric_with_labels:
                metric_name = metric_with_labels.split('{')[0]
                label_part = metric_with_labels.split('{')[1].split('}')[0]
                
                labels = {}
                for pair in label_part.split(','):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        labels[k.strip()] = v.strip('"')
                
                # Store in nested structure
                if metric_name not in result:
                    result[metric_name] = []
                result[metric_name].append({
                    "labels": labels,
                    "value": value
                })
            else:
                # Metric without labels
                if metric_with_labels not in result:
                    result[metric_with_labels] = []
                result[metric_with_labels].append({
                    "labels": {},
                    "value": value
                })
        except (ValueError, IndexError):
            continue
    
    return result

def extract_router_requests_by_flavour(metrics: Dict[str, Any]) -> Dict[str, float]:
    """Extract request counts per flavour from router metrics."""
    counts = {}
    for sample in metrics.get("router_http_requests_total", []):
        labels = sample.get("labels", {})
        if labels.get("qtype") == "queue":
            flavour = labels.get("flavour", "unknown")
            counts[flavour] = sample["value"]
    return counts

def extract_engine_metrics(metrics: Dict[str, Any], policy: str) -> Dict[str, float]:
    """Extract key metrics from decision engine."""
    result = {}
    
    # Extract credit balance
    for sample in metrics.get("scheduler_credit_balance", []):
        labels = sample.get("labels", {})
        if labels.get("policy") == policy and labels.get("namespace") == NAMESPACE:
            result["credit_balance"] = sample["value"]
            break
    
    # Extract average precision
    for sample in metrics.get("scheduler_avg_precision", []):
        labels = sample.get("labels", {})
        if labels.get("policy") == policy and labels.get("namespace") == NAMESPACE:
            result["avg_precision"] = sample["value"]
            break
    
    # Extract credit velocity
    for sample in metrics.get("scheduler_credit_velocity", []):
        labels = sample.get("labels", {})
        if labels.get("policy") == policy and labels.get("namespace") == NAMESPACE:
            result["credit_velocity"] = sample["value"]
            break
    
    # Extract current and next carbon forecast
    for sample in metrics.get("scheduler_forecast_intensity", []):
        labels = sample.get("labels", {})
        if labels.get("policy") == policy and labels.get("namespace") == NAMESPACE:
            horizon = labels.get("horizon", "")
            if horizon == "now":
                result["carbon_now"] = sample["value"]
            elif horizon == "next":
                result["carbon_next"] = sample["value"]
    
    return result

def start_locust_background(host: str, users: int, duration: str, output_dir: Path) -> subprocess.Popen:
    """Start Locust in background and return process handle."""
    locustfile = Path(__file__).parent / "locust_router.py"
    if not locustfile.exists():
        raise FileNotFoundError(f"Locustfile not found at {locustfile}")
    
    logfile = output_dir / "locust.log"
    
    proc = subprocess.Popen([
        "locust",
        "-f", str(locustfile),
        "--headless",
        "-u", str(users),
        "-r", str(LOCUST_SPAWN_RATE),
        "-t", duration,
        "--host", host,
        "--csv", str(output_dir / "locust"),
        "--only-summary",
        "--stop-timeout", "15"
    ], stdout=open(logfile, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    
    return proc

def test_policy_with_sampling(policy: str, output_dir: Path, scenario_file: Path) -> Dict[str, Any]:
    """Run full test for one policy with periodic sampling."""
    print(f"\n{'='*70}")
    print(f"Testing policy: {policy}")
    print('='*70)
    
    policy_dir = output_dir / policy
    policy_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Apply policy
    patch_policy(policy)
    
    # 2. Restart decision engine AND router to reset all metrics
    restart_decision_engine()
    restart_router()
    
    # 3. Reset mock carbon API
    reset_mock_carbon(scenario_file)
    
    # 4. Get initial state and BASELINE metrics
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
    
    # Collect BASELINE metrics (after restart, these should be ~0)
    router_metrics_baseline = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
    baseline_requests = extract_router_requests_by_flavour(router_metrics_baseline)
    
    (policy_dir / "router_metrics_baseline.txt").write_text(
        scrape_metrics(ROUTER_METRICS_URL), encoding="utf-8"
    )
    
    print(f"  ✓ Baseline collected (starting from {sum(baseline_requests.values()):.0f} requests)")

    
    # 5. Start Locust in background
    print(f"  ⏳ Starting load test: {LOCUST_USERS} users for {TEST_DURATION_MINUTES} minutes...")
    locust_proc = start_locust_background(
        ROUTER_URL, 
        LOCUST_USERS, 
        f"{TEST_DURATION_MINUTES}m", 
        policy_dir
    )
    time.sleep(5)  # Let Locust warm up
    
    # 6. Periodic sampling
    print(f"  ⏳ Sampling metrics every {SAMPLE_INTERVAL_SECONDS}s...")
    timeseries_path = policy_dir / "timeseries.csv"
    
    samples_collected = 0
    start_time = time.time()
    last_requests = {}
    
    with open(timeseries_path, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "timestamp",
            "elapsed_seconds",
            "delta_requests",
            "mean_precision",
            "credit_balance",
            "credit_velocity",
            "engine_avg_precision",
            "carbon_now",
            "carbon_next"
        ])
        
        while locust_proc.poll() is None:
            elapsed = time.time() - start_time
            
            # Check if we should stop (timeout safety)
            if elapsed > (TEST_DURATION_MINUTES * 60 + 60):
                print("  ⚠ Timeout reached, stopping...")
                locust_proc.terminate()
                break
            
            try:
                # Scrape metrics
                router_metrics = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
                engine_metrics = parse_prometheus_metrics(scrape_metrics(ENGINE_METRICS_URL))
                
                # Extract data
                current_requests = extract_router_requests_by_flavour(router_metrics)
                engine_data = extract_engine_metrics(engine_metrics, policy)
                
                # Calculate deltas
                delta_requests = {
                    k: current_requests.get(k, 0) - last_requests.get(k, 0)
                    for k in set(current_requests) | set(last_requests)
                }
                total_delta = sum(v for v in delta_requests.values() if v > 0)
                
                # Calculate weighted precision
                weighted_precision = 0.0
                if total_delta > 0:
                    for flavour, count in delta_requests.items():
                        if count > 0:
                            prec = precision_map.get(flavour, 1.0)
                            weighted_precision += (count / total_delta) * prec
                
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
            
            # Wait for next sample interval
            time.sleep(SAMPLE_INTERVAL_SECONDS)
    
    # Wait for Locust to finish
    locust_proc.wait(timeout=30)
    print(f"  ✓ Collected {samples_collected} samples")
    
    # 7. Collect final state
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
    
    # Compute delta from baseline (baseline_requests was collected before test)
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
        description="Run temporal benchmark for carbon-aware scheduling policies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all policies (40 minutes)
  python3 run_temporal_benchmark.py
  
  # Run single policy (10 minutes)
  python3 run_temporal_benchmark.py --policy credit-greedy
  
  # Run multiple policies (20 minutes)
  python3 run_temporal_benchmark.py --policy credit-greedy --policy forecast-aware
        """
    )
    parser.add_argument(
        "--policy",
        action="append",
        choices=ALL_POLICIES,
        help="Policy to test (can be specified multiple times). If not specified, all policies are tested."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Custom output directory (default: results/<timestamp>)"
    )
    
    args = parser.parse_args()
    
    # Determine which policies to test
    if args.policy:
        policies_to_test = args.policy
    else:
        policies_to_test = ALL_POLICIES
    
    print("="*70)
    print("TEMPORAL POLICY BENCHMARK TEST")
    print("="*70)
    print(f"Policies: {', '.join(policies_to_test)}")
    print(f"Duration per policy: {TEST_DURATION_MINUTES} minutes")
    print(f"Sample interval: {SAMPLE_INTERVAL_SECONDS} seconds")
    print(f"Load: {LOCUST_USERS} users @ {LOCUST_SPAWN_RATE} spawn/s")
    print(f"Total estimated time: {len(policies_to_test) * TEST_DURATION_MINUTES:.1f} minutes")
    print()
    
    # Check dependencies
    try:
        import requests  # noqa: F401
    except ImportError:
        print("ERROR: 'requests' module not installed")
        print("Install with: pip3 install requests")
        sys.exit(1)
    
    # Check for locustfile
    locustfile = Path(__file__).parent / "locust_router.py"
    if not locustfile.exists():
        print(f"ERROR: Locustfile not found at {locustfile}")
        sys.exit(1)
    
    # Verify scenario file
    scenario_file = Path(__file__).parent / "carbon_scenario.json"
    if not scenario_file.exists():
        print(f"ERROR: Scenario file not found at {scenario_file}")
        sys.exit(1)
    
    # Create output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(__file__).parent / "results" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")
    print()
    
    # Save baseline metrics once
    print("Collecting baseline metrics from all services...")
    try:
        router_baseline = scrape_metrics(ROUTER_METRICS_URL)
        (output_dir / "router_metrics_baseline.txt").write_text(router_baseline, encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ Could not collect router baseline: {e}")
    
    # Run tests for each policy
    summaries = []
    for i, policy in enumerate(policies_to_test, 1):
        try:
            print(f"\n[{i}/{len(policies_to_test)}] Starting test for {policy}...")
            summary = test_policy_with_sampling(policy, output_dir, scenario_file)
            summaries.append(summary)
        except Exception as e:
            print(f"\n  ✗ Error testing {policy}: {e}")
            import traceback
            traceback.print_exc()
            continue
        
        print(f"\n  ✓ Completed {policy}")
        
        if policy != policies_to_test[-1]:
            wait_time = 15
            print(f"\n  {'─'*70}")
            print(f"  Waiting {wait_time} seconds before next policy...")
            print(f"  {'─'*70}")
            time.sleep(wait_time)
    
    # Write aggregate summary
    (output_dir / "benchmark_summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    
    # Print comparison table
    print("\n" + "="*70)
    print("BENCHMARK SUMMARY")
    print("="*70)
    print(f"\n{'Policy':<26} {'Requests':<10} {'Precision':<12} {'Credits':<10} {'Samples':<8}")
    print("─" * 70)
    for s in summaries:
        policy = s['policy']
        reqs = int(s.get('total_requests', 0))
        prec = s.get('mean_precision', 0)
        creds = s.get('credit_balance_final')
        samples = s.get('samples_collected', 0)
        creds_str = f"{creds:.3f}" if creds is not None else "N/A"
        print(f"{policy:<26} {reqs:<10} {prec:<12.3f} {creds_str:<10} {samples:<8}")
    
    print(f"\n{'='*70}")
    print(f"Results saved to: {output_dir}")
    print(f"Check timeseries.csv in each policy folder for temporal behavior")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
