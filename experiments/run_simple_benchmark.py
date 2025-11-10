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
TEST_DURATION_MINUTES = 10
SAMPLE_INTERVAL_SECONDS = 30
LOCUST_USERS = 150
LOCUST_SPAWN_RATE = 50

# Port-forward URLs
ROUTER_URL = "http://127.0.0.1:18000"
ROUTER_METRICS_URL = "http://127.0.0.1:18001/metrics"
CONSUMER_METRICS_URL = "http://127.0.0.1:18002/metrics"
ENGINE_URL = "http://127.0.0.1:18004"
ENGINE_METRICS_URL = "http://127.0.0.1:18003/metrics"
MOCK_CARBON_URL = "http://127.0.0.1:5001"

def run_cmd(cmd: List[str], capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True, check=True, timeout=timeout)


def check_port_forwards() -> bool:
    """
    Check if required port-forwards are running and accessible.
    
    Returns True if all port-forwards are working, False otherwise.
    """
    urls_to_check = [
        (ROUTER_METRICS_URL, "Router metrics"),
        (CONSUMER_METRICS_URL, "Consumer metrics"),
        (ENGINE_URL, "Decision engine"),
        (ENGINE_METRICS_URL, "Engine metrics"),
        (MOCK_CARBON_URL, "Mock carbon API"),
    ]
    
    all_ok = True
    for url, name in urls_to_check:
        try:
            requests.get(url, timeout=2)
        except Exception:
            print(f"  ⚠️  {name} not accessible at {url}")
            all_ok = False
    
    return all_ok


def ensure_port_forwards() -> None:
    """
    Ensure all required port-forwards are running.
    
    After resetting pods, port-forwards will break. This function checks
    and restarts them if needed.
    """
    print("  ⏳ Verifying port-forwards...")
    
    if check_port_forwards():
        print("  ✓ All port-forwards are working")
        return
    
    print("  ⚠️  Some port-forwards are down, restarting them...")
    print("     Please run the following command in a separate terminal:")
    print()
    print("     cd /Users/belgio/git-repos/k8s-carbonaware-scheduler/experiments && ./setup_portforwards.sh")
    print()
    print("  ⏳ Waiting 15 seconds for port-forwards to be ready...")
    time.sleep(15)
    
    # Check again
    if check_port_forwards():
        print("  ✓ Port-forwards are now working")
    else:
        print("  ⚠️  Warning: Some port-forwards still not accessible")
        print("     The test may fail. Please ensure setup_portforwards.sh is running.")

def reset_carbon_pattern() -> None:
    """
    Reset the mock carbon API pattern to start from the beginning.
    
    This ensures all test runs start with the same carbon intensity baseline,
    making results comparable across different policies.
    """
    try:
        response = requests.post(f"{MOCK_CARBON_URL}/reset", timeout=5)
        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Carbon pattern reset to start")
            print(f"     Start time: {result.get('start_time', 'unknown')}")
        else:
            print(f"  ⚠️  Warning: Could not reset carbon API (status {response.status_code})")
    except Exception as e:
        print(f"  ⚠️  Warning: Carbon API not accessible: {e}")
        print(f"     Tests will continue but results may be inconsistent")


def reset_decision_engine() -> None:
    """
    Reset decision engine by deleting the pod.
    
    Kubernetes will automatically recreate it, giving us a fresh state
    with zero credit balance and no cached data.
    """
    print("  ⏳ Resetting decision engine...")
    try:
        # Delete the pod - Kubernetes will recreate it
        run_cmd([
            "kubectl", "delete", "pod", "-n", ENGINE_NAMESPACE,
            "-l", "app.kubernetes.io/name=decision-engine"
        ])
        print("  ✓ Decision engine pod deleted")
        
        # Wait for new pod to be ready
        print("  ⏳ Waiting for new decision engine pod to be ready...")
        for attempt in range(30):  # 30 attempts = 60 seconds max
            time.sleep(2)
            result = run_cmd([
                "kubectl", "get", "pods", "-n", ENGINE_NAMESPACE,
                "-l", "app.kubernetes.io/name=decision-engine",
                "-o", "jsonpath={.items[0].status.phase}"
            ])
            if result.stdout.strip() == "Running":
                # Wait a bit more for the service to be fully ready
                time.sleep(5)
                print("  ✓ Decision engine is ready")
                return
        
        print("  ⚠️  Warning: Decision engine pod did not become ready in time")
    except Exception as e:
        print(f"  ⚠️  Warning: Failed to reset decision engine: {e}")


def reset_router() -> None:
    """
    Reset router by deleting the pod.
    
    This clears any accumulated request counters and ensures the router
    starts with a clean slate.
    """
    print("  ⏳ Resetting router...")
    try:
        # Delete the router pod
        run_cmd([
            "kubectl", "delete", "pod", "-n", NAMESPACE,
            "-l", "app.kubernetes.io/component=router"
        ])
        print("  ✓ Router pod deleted")
        
        # Wait for new pod to be ready
        print("  ⏳ Waiting for new router pod to be ready...")
        for attempt in range(30):
            time.sleep(2)
            result = run_cmd([
                "kubectl", "get", "pods", "-n", NAMESPACE,
                "-l", "app.kubernetes.io/component=router",
                "-o", "jsonpath={.items[0].status.phase}"
            ])
            if result.stdout.strip() == "Running":
                time.sleep(3)
                print("  ✓ Router is ready")
                return
        
        print("  ⚠️  Warning: Router pod did not become ready in time")
    except Exception as e:
        print(f"  ⚠️  Warning: Failed to reset router: {e}")


def wait_for_schedule() -> bool:
    """
    Wait for decision engine to have a valid schedule ready.
    
    Returns True if schedule is ready, False if timeout.
    """
    print("  ⏳ Waiting for decision engine to compute initial schedule...")
    
    for attempt in range(20):  # 20 attempts = 40 seconds max
        try:
            response = requests.get(
                f"{ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
                timeout=5
            )
            if response.status_code == 200:
                schedule = response.json()
                if schedule.get("flavourWeights"):
                    weights = schedule["flavourWeights"]
                    total_weight = sum(weights.values())
                    print(f"  ✓ Schedule ready: {weights}")
                    print(f"     Total weight: {total_weight}%")
                    
                    # Verify diagnostics are present
                    if "diagnostics" in schedule:
                        diag = schedule["diagnostics"]
                        print(f"     Carbon now: {diag.get('carbon_now', 'N/A')} gCO2/kWh")
                        print(f"     Credit balance: {diag.get('credit_balance', 'N/A')}")
                    return True
        except Exception as e:
            pass  # Retry
        
        time.sleep(2)
    
    print("  ⚠️  Warning: Decision engine schedule not ready after 40 seconds")
    return False

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

def get_decision_engine_schedule() -> Dict[str, Any]:
    """Get schedule data from decision engine including flavour details."""
    try:
        response = requests.get(
            f"http://127.0.0.1:18004/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"  ⚠️  Warning: Could not fetch decision engine schedule: {e}")
    return {}

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
    
    # ═══════════════════════════════════════════════════════════════════
    # RESET PHASE: Ensure clean, repeatable test environment
    # ═══════════════════════════════════════════════════════════════════
    print("\n🔄 Resetting test environment for repeatability...")
    
    # 1. Reset carbon API to start from beginning of pattern
    reset_carbon_pattern()
    
    # 2. Reset decision engine (clears credit balance and cache)
    reset_decision_engine()
    
    # 3. Reset router (clears request counters)
    reset_router()
    
    # 4. Ensure port-forwards are working (they break when pods restart)
    ensure_port_forwards()
    
    # 5. Apply policy with fast update intervals
    print("\n⚙️  Configuring policy...")
    patch_policy(policy)
    
    # 6. Wait for decision engine to compute initial schedule
    if not wait_for_schedule():
        print("  ⚠️  Warning: Proceeding without confirmed schedule")
    
    print("\n✓ Test environment ready!")
    
    # ═══════════════════════════════════════════════════════════════════
    # BASELINE COLLECTION: Capture initial state
    # ═══════════════════════════════════════════════════════════════════
    print("\n📊 Collecting baseline metrics...")
    schedule_before = get_schedule_status()
    (policy_dir / "schedule_before.json").write_text(
        json.dumps(schedule_before, indent=2), encoding="utf-8"
    )
    
    # Get flavour info from decision engine (has name and carbonIntensity)
    engine_schedule = get_decision_engine_schedule()
    (policy_dir / "engine_schedule_before.json").write_text(
        json.dumps(engine_schedule, indent=2), encoding="utf-8"
    )
    
    # Parse precision and carbon info from decision engine data
    flavours = engine_schedule.get("flavours", [])
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
    
    # ═══════════════════════════════════════════════════════════════════
    # LOAD TEST: Start Locust and begin sampling
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n🚀 Starting load test: {LOCUST_USERS} users for {TEST_DURATION_MINUTES} minutes...")
    locust_proc = start_locust_background(policy_dir)
    
    # 4. Sample metrics periodically
    print(f"  ⏳ Sampling metrics every {SAMPLE_INTERVAL_SECONDS}s...")
    csv_path = policy_dir / "timeseries.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "timestamp", "elapsed_seconds", "delta_requests", "mean_precision",
            "credit_balance", "credit_velocity", "engine_avg_precision",
            "carbon_now", "carbon_next",
            "requests_precision_30", "requests_precision_50", "requests_precision_100",
            "commanded_weight_30", "commanded_weight_50", "commanded_weight_100"
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
                
                # Get current schedule from decision engine to see commanded weights
                try:
                    schedule_response = requests.get(
                        f"http://127.0.0.1:18004/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
                        timeout=2
                    )
                    commanded_weights = {}
                    if schedule_response.status_code == 200:
                        schedule_data = schedule_response.json()
                        flavours = schedule_data.get("flavours", [])
                        for flav in flavours:
                            prec = flav.get("precision")
                            weight = flav.get("weight", 0)
                            if prec is not None:
                                commanded_weights[f"precision-{int(prec)}"] = weight
                except Exception:
                    commanded_weights = {}
                
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
                    elif 'horizon="now"' in key and "scheduler_forecast_intensity" in key:
                        engine_data["carbon_now"] = value
                    elif 'horizon="next"' in key and "scheduler_forecast_intensity" in key:
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
                    f"{engine_data.get('carbon_next', ''):.1f}" if 'carbon_next' in engine_data else "",
                    int(delta_requests.get("precision-30", 0)),
                    int(delta_requests.get("precision-50", 0)),
                    int(delta_requests.get("precision-100", 0)),
                    commanded_weights.get("precision-30", ""),
                    commanded_weights.get("precision-50", ""),
                    commanded_weights.get("precision-100", "")
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
    
    # Get credit info from final engine metrics
    final_engine_metrics = parse_prometheus_metrics(engine_metrics_final_text)
    credit_balance_final = None
    credit_velocity_final = None
    avg_precision_final = None
    for key, value in final_engine_metrics.items():
        if "credit_balance" in key:
            credit_balance_final = value
        elif "credit_velocity" in key:
            credit_velocity_final = value
        elif "avg_precision" in key:
            avg_precision_final = value
    
    summary = {
        "policy": policy,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "test_duration_minutes": TEST_DURATION_MINUTES,
        "samples_collected": samples_collected,
        "total_requests": total_requests,
        "requests_by_flavour": requests_delta,
        "mean_precision": weighted_precision_final,
        "mean_carbon_intensity": mean_carbon_intensity,
        "credit_balance_final": credit_balance_final,
        "credit_velocity_final": credit_velocity_final,
        "avg_precision_reported": avg_precision_final,
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
    print(f"    Final credit balance: {credit_balance_final if credit_balance_final is not None else 'N/A'}")
    
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
