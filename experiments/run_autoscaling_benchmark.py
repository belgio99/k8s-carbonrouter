#!/usr/bin/env python3
"""
Autoscaling benchmark: Compare forecast-aware-global with and without throttling.

This benchmark demonstrates the carbon savings from throttling-based temporal shifting
by comparing:
- forecast-aware-global (with throttling): Limits replicas during high carbon, queues build
- forecast-aware-global (no throttling): Scales freely, no carbon-aware throttling

Uses a ramping load pattern to stress-test the autoscaling behavior:
- 0-2 min: Ramp 50→250 users (spike during high carbon period)
- 2-5 min: Hold at 250 users (sustained pressure)
- 5-10 min: Drop to 100 users (steady state during low carbon)

Carbon scenario:
- 0-2 min: HIGH carbon 280-400 gCO₂/kWh (throttling should activate)
- 2-4 min: TRANSITION 400→120 gCO₂/kWh (rapid drop)
- 4-10 min: LOW carbon 60-110 gCO₂/kWh (queues process)
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

# Test strategies: (policy_name, config_overrides, directory_suffix)
STRATEGIES = [
    ("forecast-aware-global", {"throttleMin": "0.2"}, "with-throttle"),  # Normal throttling
    ("forecast-aware-global", {"throttleMin": "1.0"}, "no-throttle"),  # Throttling disabled
]

NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"
ENGINE_NAMESPACE = "carbonrouter-system"

# Test configuration
TEST_DURATION_MINUTES = 10
SAMPLE_INTERVAL_SECONDS = 5
CARBON_SCENARIO = "carbon_scenario_autoscaling.json"
LOCUST_FILE = "locust_ramping.py"

# Port-forward URLs
ROUTER_URL = "http://127.0.0.1:18000"
ROUTER_METRICS_URL = "http://127.0.0.1:18001/metrics"
CONSUMER_METRICS_URL = "http://127.0.0.1:18002/metrics"
ENGINE_URL = "http://127.0.0.1:18004"
ENGINE_METRICS_URL = "http://127.0.0.1:18003/metrics"
MOCK_CARBON_URL = "http://127.0.0.1:5001"
PROMETHEUS_URL = "http://127.0.0.1:19090"


def run_cmd(cmd: List[str], capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True, check=True, timeout=timeout)


def check_port_forwards() -> bool:
    """Check if required port-forwards are running."""
    urls = [
        (ROUTER_METRICS_URL, "Router metrics"),
        (CONSUMER_METRICS_URL, "Consumer metrics"),
        (ENGINE_URL, "Decision engine"),
        (ENGINE_METRICS_URL, "Engine metrics"),
        (MOCK_CARBON_URL, "Mock carbon API"),
    ]

    all_ok = True
    for url, name in urls:
        try:
            requests.get(url, timeout=2)
        except Exception:
            print(f"  ⚠️  {name} not accessible at {url}")
            all_ok = False

    return all_ok


def ensure_port_forwards() -> None:
    """Ensure all required port-forwards are running."""
    print("  ⏳ Verifying port-forwards...")

    if check_port_forwards():
        print("  ✓ All port-forwards are working")
        return

    print("  ⚠️  Some port-forwards are down, restarting them...")
    script_path = Path(__file__).parent / "setup_portforwards.sh"

    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )

        if result.returncode == 0:
            print("  ✓ Port-forwards restarted successfully")
        else:
            print(f"  ⚠️  Port-forward script failed: {result.stderr}")
    except Exception as e:
        print(f"  ⚠️  Error restarting port-forwards: {e}")


def reset_carbon_pattern() -> None:
    """Reset the mock carbon API pattern."""
    try:
        # Check if carbon API is running
        response = requests.get(f"{MOCK_CARBON_URL}/health", timeout=2)
        if response.status_code != 200:
            print(f"  ⚠️  Carbon API health check failed")
            return

        # Reset to beginning
        response = requests.post(f"{MOCK_CARBON_URL}/reset", timeout=5)
        if response.status_code == 200:
            result = response.json()
            print(f"  ✓ Carbon pattern reset to start")
            scenario = result.get("scenario", "unknown")
            if scenario:
                print(f"     Scenario: {scenario}")
        elif response.status_code == 404:
            print(f"  ⚠️  Carbon API /reset endpoint not available")
            print(f"     Continuing without reset...")
        else:
            print(f"  ⚠️  Could not reset carbon API (status {response.status_code})")
            print(f"     Continuing anyway...")
    except requests.exceptions.ConnectionError:
        print(f"  ⚠️  Carbon API not running at {MOCK_CARBON_URL}")
        print(f"     Start it with:")
        print(f"     cd tests && python3 mock-carbon-api.py --scenario custom --file ../experiments/carbon_scenario.json --port 5001 &")
        sys.exit(1)
    except Exception as e:
        print(f"  ⚠️  Carbon API error: {e}")
        print(f"     Continuing anyway...")


def reset_decision_engine() -> None:
    """Reset decision engine by deleting the pod."""
    print("  ⏳ Resetting decision engine...")
    try:
        run_cmd([
            "kubectl", "delete", "pod", "-n", ENGINE_NAMESPACE,
            "-l", "app.kubernetes.io/name=decision-engine"
        ])
        print("  ✓ Decision engine pod deleted")

        print("  ⏳ Waiting for new decision engine pod...")
        for _ in range(30):
            time.sleep(2)
            result = run_cmd([
                "kubectl", "get", "pods", "-n", ENGINE_NAMESPACE,
                "-l", "app.kubernetes.io/name=decision-engine",
                "-o", "jsonpath={.items[0].status.phase}"
            ])
            if result.stdout.strip() == "Running":
                time.sleep(5)
                print("  ✓ Decision engine is ready")
                return

        print("  ⚠️  Decision engine pod did not become ready in time")
    except Exception as e:
        print(f"  ⚠️  Failed to reset decision engine: {e}")


def reset_operator() -> None:
    """Reset operator by deleting the pod."""
    print("  ⏳ Resetting operator...")
    try:
        run_cmd([
            "kubectl", "delete", "pod", "-n", ENGINE_NAMESPACE,
            "-l", "control-plane=controller-manager"
        ])
        print("  ✓ Operator pod deleted")

        print("  ⏳ Waiting for new operator pod...")
        for _ in range(30):
            time.sleep(2)
            result = run_cmd([
                "kubectl", "get", "pods", "-n", ENGINE_NAMESPACE,
                "-l", "control-plane=controller-manager",
                "-o", "jsonpath={.items[0].status.phase}"
            ])
            if result.stdout.strip() == "Running":
                time.sleep(3)
                print("  ✓ Operator is ready")
                return

        print("  ⚠️  Operator pod did not become ready in time")
    except Exception as e:
        print(f"  ⚠️  Failed to reset operator: {e}")


def wait_for_schedule() -> bool:
    """Wait for decision engine to have a valid schedule ready."""
    print("  ⏳ Waiting for decision engine schedule...")

    for _ in range(20):
        try:
            response = requests.get(
                f"{ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
                timeout=5
            )
            if response.status_code == 200:
                schedule = response.json()
                if schedule.get("flavourWeights"):
                    weights = schedule["flavourWeights"]
                    print(f"  ✓ Schedule ready: {weights}")
                    return True
        except Exception:
            pass
        time.sleep(2)

    print("  ⚠️  Schedule not ready after 40 seconds")
    return False


def patch_policy(policy: str, config_overrides: Dict[str, str]) -> None:
    """Update TrafficSchedule with new policy and configuration."""
    patch_data = {
        "spec": {
            "scheduler": {
                "policy": policy,
                "validFor": 5,
                "carbonCacheTTL": 5
            }
        }
    }

    # Add config overrides (like throttleMin)
    for key, value in config_overrides.items():
        patch_data["spec"]["scheduler"][key] = value

    patch = json.dumps(patch_data)
    run_cmd([
        "kubectl", "patch", "trafficschedule", SCHEDULE_NAME,
        "-n", NAMESPACE, "--type=merge", f"-p={patch}"
    ])
    print(f"  ✓ Patched policy to {policy}")
    print(f"     Config overrides: {config_overrides}")
    print("  ⏳ Waiting 30s for stabilization...")
    time.sleep(30)


def scrape_metrics(url: str) -> str:
    """Fetch Prometheus metrics from URL."""
    try:
        response = requests.get(url, timeout=10)
        return response.text
    except requests.exceptions.ConnectionError:
        return "# Metrics unavailable (connection refused)\n"


def parse_prometheus_metrics(text: str) -> Dict[str, float]:
    """Parse Prometheus text format into dict."""
    metrics = {}
    for line in text.split('\n'):
        if line and not line.startswith('#'):
            parts = line.split()
            if len(parts) >= 2:
                metrics[parts[0]] = float(parts[1])
    return metrics


def query_prometheus(query: str, warn_on_empty: bool = False) -> float:
    """Execute a PromQL query and return the scalar result."""
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            result = data.get("data", {}).get("result", [])
            if result and len(result) > 0:
                value = result[0].get("value", [None, 0])
                return float(value[1]) if len(value) > 1 else 0.0
            elif warn_on_empty:
                print(f"    ⚠️  Empty result for query: {query[:80]}...")
        return 0.0
    except Exception as e:
        if warn_on_empty:
            print(f"    ⚠️  Query failed: {e}")
        return 0.0


def get_kubectl_replica_counts() -> Dict[str, int]:
    """Get replica counts directly from kubectl (fallback for Prometheus)."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployments", "-n", NAMESPACE, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            replicas = {}
            for deployment in data.get("items", []):
                name = deployment["metadata"]["name"]
                count = deployment["status"].get("replicas", 0)
                if "router" in name:
                    replicas["router"] = count
                elif "consumer" in name:
                    replicas["consumer"] = count
                elif "carbonstat-precision" in name:
                    replicas.setdefault("target", 0)
                    replicas["target"] += count
            return replicas
    except Exception:
        pass
    return {"router": 0, "consumer": 0, "target": 0}


def get_rabbitmq_queue_depths() -> Dict[str, int]:
    """Get queue depths via kubectl exec to RabbitMQ pod."""
    try:
        result = subprocess.run(
            [
                "kubectl", "exec", "-n", "carbonrouter-system",
                "carbonrouter-rabbitmq-0", "--",
                "rabbitmqctl", "list_queues", "name", "messages_ready", "-q"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            depths = {
                "total": 0,
                "p30": 0,
                "p50": 0,
                "p100": 0
            }
            for line in result.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2:
                    name = parts[0]
                    try:
                        messages = int(parts[1])
                    except ValueError:
                        continue
                    depths["total"] += messages
                    if "precision-30" in name:
                        depths["p30"] += messages
                    elif "precision-50" in name:
                        depths["p50"] += messages
                    elif "precision-100" in name:
                        depths["p100"] += messages
            return depths
    except Exception:
        pass
    return {"total": 0, "p30": 0, "p50": 0, "p100": 0}


def extract_router_requests_by_flavour(metrics: Dict[str, float]) -> Dict[str, float]:
    """Extract request counts per flavour from router metrics."""
    requests_by_flavour = {}
    for key, value in metrics.items():
        if (key.startswith('router_http_requests_total{') and
            'flavour=' in key and
            'method="POST"' in key and
            'status="200"' in key):
            flavour_start = key.find('flavour="') + 9
            flavour_end = key.find('"', flavour_start)
            flavour = key[flavour_start:flavour_end]
            requests_by_flavour[flavour] = value
    return requests_by_flavour


def get_decision_engine_schedule() -> Dict[str, Any]:
    """Get schedule data from decision engine."""
    try:
        response = requests.get(
            f"{ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return {}


def start_locust_background(policy_dir: Path) -> subprocess.Popen:
    """Start Locust with ramping load shape."""
    locustfile = Path(__file__).parent / LOCUST_FILE
    cmd = [
        "locust",
        "-f", str(locustfile),
        "--headless",
        f"--run-time={TEST_DURATION_MINUTES}m",
        f"--csv={policy_dir / 'locust'}",
        f"--logfile={policy_dir / 'locust.log'}",
        "--host", ROUTER_URL
    ]
    return subprocess.Popen(
        cmd,
        env={**subprocess.os.environ, "BENCHMARK_PATH": "/avg"},
        stderr=subprocess.DEVNULL
    )


def test_strategy(policy: str, config_overrides: Dict[str, str], output_dir: Path, dir_suffix: str = "") -> Dict[str, Any]:
    """Test a single strategy with sampling."""
    dir_name = f"{policy.replace('/', '_')}-{dir_suffix}" if dir_suffix else policy.replace("/", "_")
    policy_dir = output_dir / dir_name
    policy_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"Testing: {policy}")
    print(f"Config: {config_overrides}")
    print(f"Dir: {dir_name}")
    print(f"{'='*70}")

    # Reset environment
    print("\n🔄 Resetting environment...")
    reset_carbon_pattern()
    reset_decision_engine()
    reset_operator()
    ensure_port_forwards()

    # Apply policy
    print("\n⚙️  Configuring policy...")
    patch_policy(policy, config_overrides)

    if not wait_for_schedule():
        print("  ⚠️  Proceeding without confirmed schedule")

    print("\n✓ Environment ready!")

    # Collect baseline
    print("\n📊 Collecting baseline...")
    engine_schedule = get_decision_engine_schedule()
    (policy_dir / "engine_schedule_before.json").write_text(
        json.dumps(engine_schedule, indent=2), encoding="utf-8"
    )

    # Parse flavour info
    flavours = engine_schedule.get("flavours", [])
    precision_map = {}
    carbon_map = {}
    for f in flavours:
        name = f.get("name", "")
        prec = f.get("precision", 100)
        carbon = f.get("carbonIntensity", 0)
        if isinstance(prec, (int, float)):
            precision_map[name] = float(prec) / 100.0 if prec > 1 else float(prec)
        if isinstance(carbon, (int, float)):
            carbon_map[name] = float(carbon)

    router_metrics_baseline = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
    baseline_requests = extract_router_requests_by_flavour(router_metrics_baseline)

    (policy_dir / "router_metrics_baseline.txt").write_text(
        scrape_metrics(ROUTER_METRICS_URL), encoding="utf-8"
    )

    print(f"  ✓ Baseline collected")

    # Start load test
    print(f"\n🚀 Starting ramping load test ({TEST_DURATION_MINUTES} minutes)...")
    print("     0-2 min: 50→250 users (spike)")
    print("     2-5 min: 250 users (hold)")
    print("     5-10 min: 100 users (steady)")

    locust_proc = start_locust_background(policy_dir)

    # Sample metrics
    print(f"  ⏳ Sampling every {SAMPLE_INTERVAL_SECONDS}s...")
    csv_path = policy_dir / "timeseries.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "timestamp", "elapsed_seconds", "delta_requests", "mean_precision",
            "credit_balance", "credit_velocity", "engine_avg_precision",
            "carbon_now", "carbon_next",
            "requests_precision_30", "requests_precision_50", "requests_precision_100",
            "commanded_weight_30", "commanded_weight_50", "commanded_weight_100",
            "queue_depth_total", "queue_depth_p30", "queue_depth_p50", "queue_depth_p100",
            "replicas_router", "replicas_consumer", "replicas_target",
            "ceiling_router", "ceiling_consumer", "ceiling_target",
            "throttle_factor"
        ])
        csvfile.flush()

        start_time = time.time()
        samples_collected = 0
        last_requests = baseline_requests.copy()

        while locust_proc.poll() is None:
            try:
                time.sleep(SAMPLE_INTERVAL_SECONDS)
                elapsed = time.time() - start_time

                # Collect metrics
                router_metrics = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
                engine_metrics = parse_prometheus_metrics(scrape_metrics(ENGINE_METRICS_URL))

                # Get schedule for commanded weights and ceilings
                try:
                    schedule_response = requests.get(
                        f"{ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
                        timeout=2
                    )
                    commanded_weights = {}
                    effective_ceilings = {}
                    throttle_factor = 0.0
                    if schedule_response.status_code == 200:
                        schedule_data = schedule_response.json()
                        flavours_list = schedule_data.get("flavours", [])
                        for flav in flavours_list:
                            prec = flav.get("precision")
                            weight = flav.get("weight", 0)
                            if prec is not None:
                                commanded_weights[f"precision-{int(prec)}"] = weight

                        processing = schedule_data.get("processing", {})
                        effective_ceilings = processing.get("ceilings", {})
                        throttle_factor = processing.get("throttle", 0.0)
                except Exception:
                    commanded_weights = {}
                    effective_ceilings = {}
                    throttle_factor = 0.0

                # Try RabbitMQ Management API first (more reliable)
                queue_depths = get_rabbitmq_queue_depths()
                queue_depth_total = queue_depths["total"]
                queue_depth_p30 = queue_depths["p30"]
                queue_depth_p50 = queue_depths["p50"]
                queue_depth_p100 = queue_depths["p100"]

                # Try kubectl first (more reliable than Prometheus queries)
                kubectl_replicas = get_kubectl_replica_counts()
                replicas_router = kubectl_replicas["router"]
                replicas_consumer = kubectl_replicas["consumer"]
                replicas_target = kubectl_replicas["target"]

                # Fallback to Prometheus if kubectl failed (all zeros)
                if replicas_router == 0 and replicas_consumer == 0 and replicas_target == 0:
                    replicas_router = query_prometheus(f'sum(kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment=~".*router.*"}})')
                    replicas_consumer = query_prometheus(f'sum(kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment=~".*consumer.*"}})')
                    replicas_target = query_prometheus(f'sum(kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment=~"carbonstat-precision.*"}})')

                current_requests = extract_router_requests_by_flavour(router_metrics)

                # Calculate delta
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
                    commanded_weights.get("precision-100", ""),
                    int(queue_depth_total),
                    int(queue_depth_p30),
                    int(queue_depth_p50),
                    int(queue_depth_p100),
                    int(replicas_router),
                    int(replicas_consumer),
                    int(replicas_target),
                    effective_ceilings.get("router", ""),
                    effective_ceilings.get("consumer", ""),
                    effective_ceilings.get("target", ""),
                    f"{throttle_factor:.4f}" if isinstance(throttle_factor, (int, float)) else ""
                ])
                csvfile.flush()

                last_requests = current_requests
                samples_collected += 1

                if samples_collected % 5 == 0:
                    print(f"    Sample {samples_collected}: {int(total_delta)} req/period, "
                          f"prec={weighted_precision:.3f}, "
                          f"queue={int(queue_depth_total)}, "
                          f"replicas={int(replicas_consumer+replicas_target)}, "
                          f"throttle={throttle_factor:.2f}")

            except Exception as e:
                print(f"  ⚠ Sampling error: {e}")

    locust_proc.wait(timeout=30)
    print(f"  ✓ Collected {samples_collected} samples")

    # Collect final metrics
    print("  ⏳ Collecting final metrics...")
    time.sleep(5)

    (policy_dir / "router_metrics_final.txt").write_text(scrape_metrics(ROUTER_METRICS_URL), encoding="utf-8")
    try:
        (policy_dir / "consumer_metrics_final.txt").write_text(scrape_metrics(CONSUMER_METRICS_URL), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  Consumer metrics unavailable: {e}")
    (policy_dir / "engine_metrics_final.txt").write_text(scrape_metrics(ENGINE_METRICS_URL), encoding="utf-8")

    final_router_metrics = parse_prometheus_metrics(scrape_metrics(ROUTER_METRICS_URL))
    final_requests = extract_router_requests_by_flavour(final_router_metrics)

    requests_delta = {
        k: final_requests.get(k, 0) - baseline_requests.get(k, 0)
        for k in set(final_requests) | set(baseline_requests)
    }
    total_requests = sum(v for v in requests_delta.values() if v > 0)

    weighted_precision_final = 0.0
    mean_carbon = 0.0
    if total_requests > 0:
        for flavour, count in requests_delta.items():
            if count > 0:
                prec = precision_map.get(flavour, 1.0)
                carbon = carbon_map.get(flavour, 0.0)
                weighted_precision_final += (count / total_requests) * prec
                mean_carbon += (count / total_requests) * carbon

    summary = {
        "policy": policy,
        "config_overrides": config_overrides,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "test_duration_minutes": TEST_DURATION_MINUTES,
        "samples_collected": samples_collected,
        "total_requests": total_requests,
        "requests_by_flavour": requests_delta,
        "mean_precision": weighted_precision_final,
        "mean_carbon_intensity": mean_carbon,
    }

    (policy_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print("\n  Results:")
    print(f"    Samples: {samples_collected}")
    print(f"    Total requests: {int(total_requests)}")
    print(f"    Mean precision: {weighted_precision_final:.3f}")
    print(f"    Mean carbon: {mean_carbon:.3f}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Autoscaling benchmark: Compare throttling vs no-throttling",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--strategies",
        choices=["both", "throttle", "no-throttle"],
        default="both",
        help="Which strategies to test (default: both)"
    )

    args = parser.parse_args()

    print("="*70)
    print("AUTOSCALING BENCHMARK: Throttling vs No-Throttling")
    print("="*70)
    print(f"Duration: {TEST_DURATION_MINUTES} minutes")
    print(f"Sample interval: {SAMPLE_INTERVAL_SECONDS} seconds")
    print(f"Carbon scenario: {CARBON_SCENARIO}")
    print(f"Load pattern: Ramping (50→250→100 users)")
    print()

    # Determine which strategies to run
    strategies_to_run = []
    if args.strategies in ["both", "throttle"]:
        strategies_to_run.append(STRATEGIES[0])
    if args.strategies in ["both", "no-throttle"]:
        strategies_to_run.append(STRATEGIES[1])

    # Create output directory
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results" / f"autoscaling_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}\n")

    # Run tests
    summaries = []
    try:
        for policy, config_overrides, dir_suffix in strategies_to_run:
            summary = test_strategy(policy, config_overrides, output_dir, dir_suffix)
            summaries.append(summary)

        # Save comparison
        comparison = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "strategies": summaries
        }
        (output_dir / "comparison.json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8"
        )

        print("\n" + "="*70)
        print("✅ Benchmark completed!")
        print(f"Results: {output_dir}")
        print("="*70)

        # Print comparison
        if len(summaries) == 2:
            print("\n📊 COMPARISON:")
            print(f"  With throttling:    {summaries[0]['total_requests']:.0f} req, "
                  f"prec={summaries[0]['mean_precision']:.3f}, "
                  f"carbon={summaries[0]['mean_carbon_intensity']:.3f}")
            print(f"  Without throttling: {summaries[1]['total_requests']:.0f} req, "
                  f"prec={summaries[1]['mean_precision']:.3f}, "
                  f"carbon={summaries[1]['mean_carbon_intensity']:.3f}")

            if summaries[0]['mean_carbon_intensity'] > 0 and summaries[1]['mean_carbon_intensity'] > 0:
                carbon_savings = (1 - summaries[0]['mean_carbon_intensity'] / summaries[1]['mean_carbon_intensity']) * 100
                print(f"\n  💚 Carbon savings: {carbon_savings:+.1f}%")

        return 0
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
