#!/usr/bin/env python3
"""Simplified policy benchmark runner for thesis experiments.

Tests each of the 3 carbon-aware policies by:
1. Patching the TrafficSchedule to use the policy
2. Restarting the decision engine to reset credits
3. Running a short Locust test (2 minutes)
4. Collecting metrics before and after

Results are saved to experiments/results/<timestamp>/.
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
import sys
import requests

POLICIES = ["credit-greedy", "forecast-aware", "forecast-aware-global"]
NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"
ENGINE_NAMESPACE = "carbonrouter-system"
ENGINE_DEPLOYMENT = "carbonrouter-decision-engine"
MOCK_CARBON_URL = "http://127.0.0.1:5001"

def run_cmd(cmd: List[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True, check=True)

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


def patch_policy(policy: str) -> None:
    """Update TrafficSchedule with new policy."""
    patch = json.dumps({
        "spec": {
            "scheduler": {
                "policy": policy,
                "validFor": 30,
                "carbonCacheTTL": 15
            }
        }
    })
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
        "-n", ENGINE_NAMESPACE, "--timeout=60s"
    ])
    print("  ✓ Decision engine ready")
    time.sleep(5)  # Extra settle time

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

def extract_key_metrics(metrics_text: str) -> Dict[str, float]:
    """Extract important counters from Prometheus text format."""
    result = {}
    for line in metrics_text.split('\n'):
        if line.startswith('#') or not line.strip():
            continue
        if 'router_http_requests_total{' in line and 'qtype="queue"' in line:
            # Extract flavour and value
            parts = line.split()
            if len(parts) >= 2:
                # Parse labels to find flavour
                label_part = parts[0].split('{')[1].split('}')[0]
                labels = {}
                for pair in label_part.split(','):
                    k, v = pair.split('=')
                    labels[k] = v.strip('"')
                flavour = labels.get('flavour', 'unknown')
                value = float(parts[1])
                key = f"requests_{flavour}"
                result[key] = result.get(key, 0) + value
    return result

def run_locust(host: str, users: int, duration: str, output_dir: Path) -> None:
    """Run Locust load test."""
    locustfile = Path(__file__).parent / "locust_router.py"
    if not locustfile.exists():
        print(f"  ⚠ Locustfile not found at {locustfile}, skipping load generation")
        return
    
    print(f"  ⏳ Running Locust: {users} users for {duration}...")
    try:
        subprocess.run([
            "locust",
            "-f", str(locustfile),
            "--headless",
            "-u", str(users),
            "-r", "20",
            "-t", duration,
            "--host", host,
            "--csv", str(output_dir / "locust"),
            "--only-summary",
            "--stop-timeout", "10"
        ], check=True, capture_output=True, text=True, timeout=300)
        print("  ✓ Locust completed")
    except subprocess.TimeoutExpired:
        print("  ⚠ Locust timed out")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Locust failed: {e}")
        if e.stdout:
            print(f"    stdout: {e.stdout[:200]}")
        if e.stderr:
            print(f"    stderr: {e.stderr[:200]}")

def test_policy(policy: str, output_dir: Path) -> Dict[str, Any]:
    """Run full test for one policy."""
    print(f"\n{'='*60}")
    print(f"Testing policy: {policy}")
    print('='*60)
    
    policy_dir = output_dir / policy
    policy_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Apply policy
    patch_policy(policy)
    
    # 2. Restart decision engine to reset credits
    restart_decision_engine()

    # 3. Reset carbon pattern
    reset_carbon_pattern()
    
    # 4. Get initial state
    print("  ⏳ Collecting baseline metrics...")
    schedule_before = get_schedule_status()
    (policy_dir / "schedule_before.json").write_text(
        json.dumps(schedule_before, indent=2), encoding="utf-8"
    )
    
    try:
        router_metrics_before = scrape_metrics("http://127.0.0.1:18001/metrics")
        (policy_dir / "router_metrics_before.txt").write_text(
            router_metrics_before, encoding="utf-8"
        )
        requests_before = extract_key_metrics(router_metrics_before)
    except Exception as e:
        print(f"  ⚠ Failed to collect baseline metrics: {e}")
        requests_before = {}
    
    print("  ✓ Baseline collected")
    
    # 4. Run load test
    run_locust("http://127.0.0.1:18000", users=67, duration="2m", output_dir=policy_dir)
    
    # 5. Collect final metrics
    print("  ⏳ Collecting final metrics...")
    time.sleep(5)  # Let metrics settle
    
    schedule_after = get_schedule_status()
    (policy_dir / "schedule_after.json").write_text(
        json.dumps(schedule_after, indent=2), encoding="utf-8"
    )
    
    try:
        router_metrics_after = scrape_metrics("http://127.0.0.1:18001/metrics")
        (policy_dir / "router_metrics_after.txt").write_text(
            router_metrics_after, encoding="utf-8"
        )
        requests_after = extract_key_metrics(router_metrics_after)
    except Exception as e:
        print(f"  ⚠ Failed to collect final metrics: {e}")
        requests_after = {}
    
    print("  ✓ Final metrics collected")
    
    # 6. Compute summary
    requests_delta = {
        k: requests_after.get(k, 0) - requests_before.get(k, 0)
        for k in set(requests_after) | set(requests_before)
    }
    total_requests = sum(v for v in requests_delta.values() if v > 0)
    
    # Extract precision info from schedule
    flavours = schedule_after.get("flavours", [])
    precision_map = {}
    for f in flavours:
        name = f.get("name", "")
        prec = f.get("precision", 100)
        if isinstance(prec, (int, float)):
            precision_map[name] = float(prec) / 100.0 if prec > 1 else float(prec)
    
    # Compute weighted precision
    weighted_precision = 0.0
    for flavour, count in requests_delta.items():
        if count <= 0:
            continue
        fname = flavour.replace("requests_", "")
        prec = precision_map.get(fname, 1.0)
        weighted_precision += (count / total_requests) * prec if total_requests else 0
    
    credit_info = schedule_after.get("credits", {})
    
    summary = {
        "policy": policy,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_requests": total_requests,
        "requests_by_flavour": requests_delta,
        "mean_precision": weighted_precision,
        "credit_balance": credit_info.get("balance"),
        "credit_velocity": credit_info.get("velocity"),
        "avg_precision_reported": schedule_after.get("avgPrecision"),
    }
    
    (policy_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    
    print("\n  Results:")
    print(f"    Total requests: {int(total_requests)}")
    print(f"    Mean precision: {weighted_precision:.3f}")
    print(f"    Credit balance: {credit_info.get('balance', 'N/A')}")
    print("    Requests by flavour:")
    for flavour, count in sorted(requests_delta.items()):
        if count > 0:
            fname = flavour.replace("requests_", "")
            pct = (count / total_requests * 100) if total_requests else 0
            print(f"      {fname}: {int(count)} ({pct:.1f}%)")
    
    return summary

def main():
    print("="*60)
    print("POLICY BENCHMARK TEST")
    print("="*60)
    print(f"Policies: {', '.join(POLICIES)}")
    print(f"Target: {NAMESPACE}/{SCHEDULE_NAME}")
    print()
    
    # Check dependencies
    try:
        import requests  # noqa: F401
    except ImportError:
        print("ERROR: 'requests' module not installed")
        print("Install with: pip install requests")
        sys.exit(1)
    
    # Create output directory
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")
    print()
    
    # Run tests for each policy
    summaries = []
    for policy in POLICIES:
        try:
            summary = test_policy(policy, output_dir)
            summaries.append(summary)
        except Exception as e:
            print(f"\n  ✗ Error testing {policy}: {e}")
            import traceback
            traceback.print_exc()
            continue
        
        print(f"\n  ✓ Completed {policy}")
        if policy != POLICIES[-1]:
            print(f"\n  {'─'*60}")
            print(f"  Waiting 10 seconds before next policy...")
            print(f"  {'─'*60}")
            time.sleep(10)
    
    # Write aggregate summary
    (output_dir / "benchmark_summary.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    
    # Print comparison table
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)
    print(f"\n{'Policy':<25} {'Requests':<12} {'Precision':<12} {'Credits':<10}")
    print("─" * 60)
    for s in summaries:
        policy = s['policy']
        reqs = int(s.get('total_requests', 0))
        prec = s.get('mean_precision', 0)
        creds = s.get('credit_balance', 0)
        print(f"{policy:<25} {reqs:<12} {prec:<12.3f} {creds:<10.3f}")
    
    print(f"\n{'='*60}")
    print(f"Results saved to: {output_dir}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
