#!/usr/bin/env python3
"""
Weight Evaluation Benchmark: Quantify infrastructure carbon overhead vs. savings.

This benchmark measures the carbon footprint of the carbon-aware scheduling
system itself and compares it against the carbon savings achieved through
carbon-aware scheduling decisions.

Two scenarios are compared:
1. BASELINE: No carbon-awareness (always precision-100, throttleMin=1.0)
2. CARBON-AWARE: forecast-aware-global policy with throttling (throttleMin=0.2)

The evaluation calculates:
- Infrastructure carbon cost: Energy consumed by the scheduling system components
- Workload carbon: Energy for processing requests at different precision levels
- Net carbon savings: Total savings minus infrastructure overhead
- Overhead ratio: Infrastructure cost / Net savings (should be << 1)

Key thesis defense metric: Demonstrate that carbon-aware scheduling provides
an order of magnitude more savings than the system overhead it introduces.
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
import requests

from power_calculator import PowerCalculator

# Test scenarios: (policy_name, config_overrides, description)
SCENARIOS = [
    ("baseline", {"throttleMin": "1.0"}, "baseline"),  # No carbon awareness
    ("forecast-aware-global", {"throttleMin": "0.2"}, "carbon_aware"),  # Carbon-aware
]

NAMESPACE = "carbonstat"
SCHEDULE_NAME = "traffic-schedule"
ENGINE_NAMESPACE = "carbonrouter-system"

# Test configuration
TEST_DURATION_MINUTES = 10
SAMPLE_INTERVAL_SECONDS = 5
CARBON_SCENARIO = "carbon_scenario.json"
LOCUST_FILE = "locust_router.py"

# Locust configuration (moderate steady load)
LOCUST_USERS = 100
LOCUST_SPAWN_RATE = 10

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
        response = requests.get(f"{MOCK_CARBON_URL}/health", timeout=2)
        if response.status_code != 200:
            print(f"  ⚠️  Carbon API health check failed")
            return

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


def get_replica_counts() -> Dict[str, int]:
    """Get replica counts for all infrastructure components via kubectl."""
    counts = {}

    try:
        # Always-on components (carbonrouter-system)
        result = run_cmd([
            "kubectl", "get", "pods", "-n", ENGINE_NAMESPACE,
            "--field-selector=status.phase=Running",
            "-o", "json"
        ], timeout=10)
        pods = json.loads(result.stdout)

        counts['operator'] = sum(1 for p in pods['items'] if 'controller-manager' in p['metadata']['name'])
        counts['decision_engine'] = sum(1 for p in pods['items'] if 'decision-engine' in p['metadata']['name'])
        counts['rabbitmq'] = sum(1 for p in pods['items'] if 'rabbitmq' in p['metadata']['name'])
        counts['keda'] = sum(1 for p in pods['items'] if 'keda' in p['metadata']['name'])
        counts['prometheus'] = sum(1 for p in pods['items'] if 'prometheus' in p['metadata']['name'])
        counts['grafana'] = sum(1 for p in pods['items'] if 'grafana' in p['metadata']['name'])

        # Scalable components (carbonstat namespace)
        result = run_cmd([
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "--field-selector=status.phase=Running",
            "-o", "json"
        ], timeout=10)
        pods = json.loads(result.stdout)

        counts['router'] = sum(1 for p in pods['items'] if 'buffer-service-router' in p['metadata']['name'])
        counts['consumer'] = sum(1 for p in pods['items'] if 'buffer-service-consumer' in p['metadata']['name'])
        counts['target_p30'] = sum(1 for p in pods['items'] if 'precision-30' in p['metadata']['name'])
        counts['target_p50'] = sum(1 for p in pods['items'] if 'precision-50' in p['metadata']['name'])
        counts['target_p100'] = sum(1 for p in pods['items'] if 'precision-100' in p['metadata']['name'])

    except Exception as e:
        print(f"  ⚠️  Error getting replica counts: {e}")
        # Return zeros if error
        for key in ['operator', 'decision_engine', 'rabbitmq', 'keda', 'prometheus', 'grafana',
                    'router', 'consumer', 'target_p30', 'target_p50', 'target_p100']:
            counts.setdefault(key, 0)

    return counts


def get_carbon_intensity() -> float:
    """Get current carbon intensity from decision engine."""
    try:
        response = requests.get(
            f"{ENGINE_URL}/schedule/{NAMESPACE}/{SCHEDULE_NAME}",
            timeout=5
        )
        if response.status_code == 200:
            schedule = response.json()
            return schedule.get("carbonIntensity", 0.0)
    except Exception as e:
        print(f"  ⚠️  Error getting carbon intensity: {e}")
    return 0.0


def get_request_counts() -> Dict[str, float]:
    """Get request counts per precision flavour from router metrics."""
    try:
        response = requests.get(ROUTER_METRICS_URL, timeout=5)
        if response.status_code != 200:
            return {}

        lines = response.text.split('\n')
        counts = {}

        for line in lines:
            if line.startswith('router_http_requests_total{') and 'status="200"' in line:
                if 'flavour="precision-30"' in line:
                    value = float(line.split()[-1])
                    counts['precision-30'] = value
                elif 'flavour="precision-50"' in line:
                    value = float(line.split()[-1])
                    counts['precision-50'] = value
                elif 'flavour="precision-100"' in line:
                    value = float(line.split()[-1])
                    counts['precision-100'] = value

        return counts
    except Exception as e:
        print(f"  ⚠️  Error getting request counts: {e}")
        return {}


def get_engine_metrics() -> Dict[str, Any]:
    """Get metrics from decision engine."""
    try:
        response = requests.get(ENGINE_METRICS_URL, timeout=5)
        if response.status_code != 200:
            return {}

        lines = response.text.split('\n')
        metrics = {}

        for line in lines:
            if line.startswith('scheduler_credit_balance'):
                metrics['credit_balance'] = float(line.split()[-1])
            elif line.startswith('scheduler_avg_precision'):
                metrics['avg_precision'] = float(line.split()[-1])
            elif line.startswith('scheduler_processing_throttle'):
                metrics['throttle_factor'] = float(line.split()[-1])

        return metrics
    except Exception as e:
        print(f"  ⚠️  Error getting engine metrics: {e}")
        return {}


def collect_sample(
    power_calc: PowerCalculator,
    prev_request_counts: Dict[str, float]
) -> Tuple[Dict[str, Any], Dict[str, float]]:
    """Collect a single sample of metrics."""
    sample = {}

    # Timestamp
    sample['timestamp'] = datetime.now().isoformat()

    # Replica counts
    replicas = get_replica_counts()
    sample['replicas'] = replicas

    # Carbon intensity
    sample['carbon_intensity'] = get_carbon_intensity()

    # Request counts
    curr_request_counts = get_request_counts()
    sample['request_counts'] = curr_request_counts

    # Calculate delta requests
    delta_requests = {}
    for flavour in ['precision-30', 'precision-50', 'precision-100']:
        curr = curr_request_counts.get(flavour, 0.0)
        prev = prev_request_counts.get(flavour, 0.0)
        delta_requests[flavour] = max(0, curr - prev)

    sample['delta_requests'] = delta_requests
    sample['delta_requests_total'] = sum(delta_requests.values())

    # Engine metrics
    engine_metrics = get_engine_metrics()
    sample['credit_balance'] = engine_metrics.get('credit_balance', 0.0)
    sample['avg_precision'] = engine_metrics.get('avg_precision', 0.0)
    sample['throttle_factor'] = engine_metrics.get('throttle_factor', 1.0)

    # Calculate power consumption
    total_power, power_breakdown = power_calc.calculate_total_power(
        router_replicas=replicas['router'],
        consumer_replicas=replicas['consumer'],
        target_replicas_p30=replicas['target_p30'],
        target_replicas_p50=replicas['target_p50'],
        target_replicas_p100=replicas['target_p100']
    )

    sample['total_power_watts'] = total_power
    sample['power_breakdown'] = power_breakdown

    return sample, curr_request_counts


def run_test_scenario(
    scenario_name: str,
    policy: str,
    config_overrides: Dict[str, str],
    output_dir: Path,
    power_calc: PowerCalculator,
    args: argparse.Namespace
) -> Dict[str, Any]:
    """Run a single test scenario."""
    print(f"\n{'='*70}")
    print(f"SCENARIO: {scenario_name.upper()}")
    print(f"{'='*70}")

    # Setup
    print("\n[1/6] Setup")
    patch_policy(policy, config_overrides)
    reset_decision_engine()
    reset_operator()
    reset_carbon_pattern()

    if not wait_for_schedule():
        print("  ⚠️  Schedule not ready, continuing anyway...")

    # Start Locust
    print("\n[2/6] Starting load generation")
    locust_dir = Path(__file__).parent
    locust_log = output_dir / "locust.log"

    locust_cmd = [
        "locust",
        "-f", str(locust_dir / LOCUST_FILE),
        "--host", ROUTER_URL,
        "--users", str(args.users),
        "--spawn-rate", str(args.spawn_rate),
        "--run-time", f"{args.duration}m",
        "--headless",
        "--csv", str(output_dir / "locust_stats"),
        "--html", str(output_dir / "locust_report.html")
    ]

    with open(locust_log, 'w') as f:
        locust_proc = subprocess.Popen(
            locust_cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd=str(locust_dir)
        )

    print(f"  ✓ Locust started with {args.users} users")
    print(f"     Test duration: {args.duration} minutes")
    print("  ⏳ Waiting 15s for warmup...")
    time.sleep(15)

    # Data collection
    print("\n[3/6] Collecting metrics")
    samples = []
    prev_request_counts = {}

    num_samples = int((args.duration * 60) / args.sample_interval)
    start_time = time.time()

    for i in range(num_samples):
        elapsed = time.time() - start_time
        remaining = (args.duration * 60) - elapsed
        progress = (i / num_samples) * 100

        print(f"  [{progress:5.1f}%] Sample {i+1}/{num_samples} | "
              f"Elapsed: {elapsed/60:.1f}m | Remaining: {remaining/60:.1f}m", end='\r')

        sample, prev_request_counts = collect_sample(power_calc, prev_request_counts)
        sample['elapsed_seconds'] = elapsed
        samples.append(sample)

        time.sleep(args.sample_interval)

    print(f"  [100.0%] Data collection complete ({len(samples)} samples)")

    # Stop Locust
    print("\n[4/6] Stopping load generation")
    locust_proc.terminate()
    try:
        locust_proc.wait(timeout=10)
        print("  ✓ Locust stopped gracefully")
    except subprocess.TimeoutExpired:
        locust_proc.kill()
        print("  ⚠️  Locust force-killed")

    # Save timeseries
    print("\n[5/6] Saving results")
    save_timeseries(samples, output_dir / "timeseries.csv")

    # Calculate summary
    print("\n[6/6] Calculating summary")
    summary = calculate_summary(scenario_name, policy, config_overrides, samples, power_calc)
    save_summary(summary, output_dir / "summary.json")

    print(f"\n  ✓ Scenario complete: {scenario_name}")
    print(f"     Total requests: {summary['total_requests']:.0f}")
    print(f"     Mean precision: {summary['mean_precision']:.4f}")
    print(f"     Total infrastructure carbon: {summary['total_infrastructure_carbon_g']:.2f} gCO2")

    return summary


def save_timeseries(samples: List[Dict[str, Any]], output_path: Path) -> None:
    """Save timeseries data to CSV."""
    if not samples:
        return

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'timestamp',
            'elapsed_seconds',
            'carbon_intensity',
            'delta_requests_total',
            'delta_requests_p30',
            'delta_requests_p50',
            'delta_requests_p100',
            'credit_balance',
            'avg_precision',
            'throttle_factor',
            'total_power_watts',
            'power_always_on',
            'power_router',
            'power_consumer',
            'power_target_p30',
            'power_target_p50',
            'power_target_p100',
            'replicas_operator',
            'replicas_decision_engine',
            'replicas_rabbitmq',
            'replicas_keda',
            'replicas_prometheus',
            'replicas_grafana',
            'replicas_router',
            'replicas_consumer',
            'replicas_target_p30',
            'replicas_target_p50',
            'replicas_target_p100'
        ])

        # Data rows
        for sample in samples:
            writer.writerow([
                sample['timestamp'],
                sample['elapsed_seconds'],
                sample['carbon_intensity'],
                sample['delta_requests_total'],
                sample['delta_requests'].get('precision-30', 0),
                sample['delta_requests'].get('precision-50', 0),
                sample['delta_requests'].get('precision-100', 0),
                sample.get('credit_balance', 0.0),
                sample.get('avg_precision', 0.0),
                sample.get('throttle_factor', 1.0),
                sample['total_power_watts'],
                sample['power_breakdown']['always_on'],
                sample['power_breakdown']['router'],
                sample['power_breakdown']['consumer'],
                sample['power_breakdown']['target_p30'],
                sample['power_breakdown']['target_p50'],
                sample['power_breakdown']['target_p100'],
                sample['replicas']['operator'],
                sample['replicas']['decision_engine'],
                sample['replicas']['rabbitmq'],
                sample['replicas']['keda'],
                sample['replicas']['prometheus'],
                sample['replicas']['grafana'],
                sample['replicas']['router'],
                sample['replicas']['consumer'],
                sample['replicas']['target_p30'],
                sample['replicas']['target_p50'],
                sample['replicas']['target_p100']
            ])

    print(f"  ✓ Timeseries saved: {output_path}")


def calculate_summary(
    scenario_name: str,
    policy: str,
    config_overrides: Dict[str, str],
    samples: List[Dict[str, Any]],
    power_calc: PowerCalculator
) -> Dict[str, Any]:
    """Calculate summary statistics from timeseries samples."""
    if not samples:
        return {}

    # Total requests
    total_requests = sum(s['delta_requests_total'] for s in samples)
    requests_by_flavour = {
        'precision-30': sum(s['delta_requests'].get('precision-30', 0) for s in samples),
        'precision-50': sum(s['delta_requests'].get('precision-50', 0) for s in samples),
        'precision-100': sum(s['delta_requests'].get('precision-100', 0) for s in samples)
    }

    # Mean precision
    if total_requests > 0:
        mean_precision = (
            requests_by_flavour['precision-30'] * 0.3 +
            requests_by_flavour['precision-50'] * 0.5 +
            requests_by_flavour['precision-100'] * 1.0
        ) / total_requests
    else:
        mean_precision = 0.0

    # Calculate cumulative infrastructure carbon
    timeseries_for_calc = []
    for sample in samples:
        timeseries_for_calc.append({
            'timestamp': sample['elapsed_seconds'],
            'total_power_watts': sample['total_power_watts'],
            'carbon_intensity': sample['carbon_intensity']
        })

    total_infrastructure_carbon_g, _ = power_calc.calculate_cumulative_carbon(
        timeseries_for_calc,
        power_breakdown_key='total_power_watts',
        carbon_intensity_key='carbon_intensity',
        timestamp_key='timestamp'
    )

    # Mean carbon intensity
    mean_carbon_intensity = sum(s['carbon_intensity'] for s in samples) / len(samples)

    # Calculate workload carbon (precision-based)
    # Using carbon intensity as proxy for workload carbon per request
    workload_carbon_g = (
        requests_by_flavour['precision-30'] * 0.3 * mean_carbon_intensity * 0.001 +
        requests_by_flavour['precision-50'] * 0.5 * mean_carbon_intensity * 0.001 +
        requests_by_flavour['precision-100'] * 1.0 * mean_carbon_intensity * 0.001
    )

    summary = {
        'scenario': scenario_name,
        'policy': policy,
        'config_overrides': config_overrides,
        'timestamp': datetime.now().isoformat(),
        'test_duration_minutes': samples[-1]['elapsed_seconds'] / 60 if samples else 0,
        'samples_collected': len(samples),
        'total_requests': total_requests,
        'requests_by_flavour': requests_by_flavour,
        'mean_precision': mean_precision,
        'mean_carbon_intensity': mean_carbon_intensity,
        'total_infrastructure_carbon_g': total_infrastructure_carbon_g,
        'workload_carbon_g': workload_carbon_g,
        'total_carbon_g': total_infrastructure_carbon_g + workload_carbon_g
    }

    return summary


def save_summary(summary: Dict[str, Any], output_path: Path) -> None:
    """Save summary to JSON file."""
    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"  ✓ Summary saved: {output_path}")


def calculate_weight_analysis(
    baseline_summary: Dict[str, Any],
    carbon_aware_summary: Dict[str, Any]
) -> Dict[str, Any]:
    """Calculate weight evaluation metrics."""
    analysis = {
        'timestamp': datetime.now().isoformat(),
        'baseline': {
            'infrastructure_carbon_g': baseline_summary['total_infrastructure_carbon_g'],
            'workload_carbon_g': baseline_summary['workload_carbon_g'],
            'total_carbon_g': baseline_summary['total_carbon_g']
        },
        'carbon_aware': {
            'infrastructure_carbon_g': carbon_aware_summary['total_infrastructure_carbon_g'],
            'workload_carbon_g': carbon_aware_summary['workload_carbon_g'],
            'total_carbon_g': carbon_aware_summary['total_carbon_g']
        }
    }

    # Carbon savings
    workload_savings = baseline_summary['workload_carbon_g'] - carbon_aware_summary['workload_carbon_g']
    infrastructure_overhead = carbon_aware_summary['total_infrastructure_carbon_g']
    net_savings = baseline_summary['total_carbon_g'] - carbon_aware_summary['total_carbon_g']

    analysis['savings'] = {
        'workload_carbon_savings_g': workload_savings,
        'infrastructure_overhead_g': infrastructure_overhead,
        'net_total_savings_g': net_savings,
        'overhead_ratio': infrastructure_overhead / workload_savings if workload_savings > 0 else float('inf'),
        'net_savings_ratio': net_savings / baseline_summary['total_carbon_g'] if baseline_summary['total_carbon_g'] > 0 else 0.0
    }

    # Convert to kg for readability
    analysis['savings_kg'] = {
        'workload_carbon_savings': workload_savings / 1000.0,
        'infrastructure_overhead': infrastructure_overhead / 1000.0,
        'net_total_savings': net_savings / 1000.0
    }

    return analysis


def print_final_report(analysis: Dict[str, Any]) -> None:
    """Print final weight evaluation report."""
    print("\n" + "="*70)
    print("WEIGHT EVALUATION REPORT")
    print("="*70)

    print("\nBASELINE (No Carbon-Awareness):")
    print(f"  Infrastructure Carbon: {analysis['baseline']['infrastructure_carbon_g']:.2f} gCO2")
    print(f"  Workload Carbon:       {analysis['baseline']['workload_carbon_g']:.2f} gCO2")
    print(f"  Total Carbon:          {analysis['baseline']['total_carbon_g']:.2f} gCO2")

    print("\nCARBON-AWARE (forecast-aware-global with throttling):")
    print(f"  Infrastructure Carbon: {analysis['carbon_aware']['infrastructure_carbon_g']:.2f} gCO2")
    print(f"  Workload Carbon:       {analysis['carbon_aware']['workload_carbon_g']:.2f} gCO2")
    print(f"  Total Carbon:          {analysis['carbon_aware']['total_carbon_g']:.2f} gCO2")

    print("\nSAVINGS:")
    print(f"  Workload Savings:      {analysis['savings']['workload_carbon_savings_g']:.2f} gCO2")
    print(f"  Infrastructure Cost:   {analysis['savings']['infrastructure_overhead_g']:.2f} gCO2")
    print(f"  Net Total Savings:     {analysis['savings']['net_total_savings_g']:.2f} gCO2")
    print(f"  Overhead Ratio:        {analysis['savings']['overhead_ratio']:.4f} ({analysis['savings']['overhead_ratio']*100:.2f}%)")
    print(f"  Net Savings Ratio:     {analysis['savings']['net_savings_ratio']:.4f} ({analysis['savings']['net_savings_ratio']*100:.2f}%)")

    print("\nKEY THESIS METRIC:")
    overhead_ratio = analysis['savings']['overhead_ratio']
    if overhead_ratio < 0.01:
        magnitude = "two orders of magnitude"
    elif overhead_ratio < 0.1:
        magnitude = "one order of magnitude"
    else:
        magnitude = "less than"

    print(f"  The carbon savings are {magnitude} larger than the infrastructure overhead.")
    print(f"  For every 1 gCO2 spent on infrastructure, we save {1/overhead_ratio:.1f} gCO2 in workload emissions.")

    print("\n" + "="*70)


def main():
    parser = argparse.ArgumentParser(description="Run weight evaluation benchmark")
    parser.add_argument("--users", type=int, default=LOCUST_USERS, help="Number of Locust users")
    parser.add_argument("--spawn-rate", type=int, default=LOCUST_SPAWN_RATE, help="Locust spawn rate")
    parser.add_argument("--duration", type=int, default=TEST_DURATION_MINUTES, help="Test duration in minutes")
    parser.add_argument("--sample-interval", type=int, default=SAMPLE_INTERVAL_SECONDS, help="Sample interval in seconds")
    parser.add_argument("--output-dir", type=str, help="Output directory (default: results/weight_evaluation_<timestamp>)")
    args = parser.parse_args()

    print("="*70)
    print("WEIGHT EVALUATION BENCHMARK")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Users: {args.users}")
    print(f"  Spawn rate: {args.spawn_rate}")
    print(f"  Duration: {args.duration} minutes")
    print(f"  Sample interval: {args.sample_interval} seconds")

    # Setup output directory
    if args.output_dir:
        output_base = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_base = Path(__file__).parent / "results" / f"weight_evaluation_{timestamp}"

    output_base.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {output_base}")

    # Initialize power calculator
    print("\nInitializing power calculator...")
    power_calc = PowerCalculator()
    print("  ✓ Power profiles loaded")

    # Pre-flight checks
    print("\nPre-flight checks...")
    ensure_port_forwards()

    # Run scenarios
    summaries = {}

    for policy, config_overrides, scenario_name in SCENARIOS:
        scenario_dir = output_base / scenario_name
        scenario_dir.mkdir(exist_ok=True)

        summary = run_test_scenario(
            scenario_name,
            policy,
            config_overrides,
            scenario_dir,
            power_calc,
            args
        )

        summaries[scenario_name] = summary

    # Calculate weight analysis
    print("\n" + "="*70)
    print("WEIGHT ANALYSIS")
    print("="*70)

    analysis = calculate_weight_analysis(
        summaries['baseline'],
        summaries['carbon_aware']
    )

    # Save analysis
    analysis_path = output_base / "weight_analysis.json"
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2)
    print(f"\n  ✓ Weight analysis saved: {analysis_path}")

    # Print final report
    print_final_report(analysis)

    print(f"\n✓ Weight evaluation complete!")
    print(f"  Results: {output_base}")
    print(f"\nNext steps:")
    print(f"  1. Generate plots: python3 experiments/plot_weight_evaluation.py {output_base}")
    print(f"  2. Review analysis: cat {analysis_path}")


if __name__ == "__main__":
    main()
