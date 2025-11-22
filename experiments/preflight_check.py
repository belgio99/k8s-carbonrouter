#!/usr/bin/env python3
"""
Pre-flight check for temporal benchmark.

Verifies that all prerequisites are met before running the benchmark.
"""

import subprocess
import sys
from pathlib import Path
import requests

def check_command(cmd: str) -> bool:
    """Check if a command is available."""
    try:
        subprocess.run([cmd, "--version"], capture_output=True, check=False)
        return True
    except FileNotFoundError:
        return False

def check_python_package(package: str) -> bool:
    """Check if a Python package is installed."""
    try:
        __import__(package)
        return True
    except ImportError:
        return False

def check_port_forward(port: int, description: str) -> bool:
    """Check if a port-forward is active."""
    try:
        response = requests.get(f"http://localhost:{port}/metrics", timeout=2)
        return response.status_code == 200
    except Exception:
        return False

def check_kubernetes_resource(kind: str, name: str, namespace: str) -> bool:
    """Check if a Kubernetes resource exists."""
    try:
        result = subprocess.run(
            ["kubectl", "get", kind, name, "-n", namespace],
            capture_output=True,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False

def check_power_profiles() -> bool:
    """Check if power profiles are valid for weight evaluation."""
    import json
    base = Path(__file__).parent
    profile_path = base / "power_profiles.json"

    if not profile_path.exists():
        print(f"   ✗ power_profiles.json NOT FOUND")
        return False

    try:
        with open(profile_path, 'r') as f:
            profiles = json.load(f)

        # Check structure
        if 'always_on_components' not in profiles:
            print(f"   ✗ Missing 'always_on_components' in power_profiles.json")
            return False

        if 'scalable_components' not in profiles:
            print(f"   ✗ Missing 'scalable_components' in power_profiles.json")
            return False

        # Validate always-on components
        required_always_on = ['operator', 'decision_engine', 'rabbitmq', 'keda', 'prometheus', 'grafana']
        for comp in required_always_on:
            if comp not in profiles['always_on_components']:
                print(f"   ✗ Missing '{comp}' in always_on_components")
                return False
            if 'power_watts' not in profiles['always_on_components'][comp]:
                print(f"   ✗ Missing 'power_watts' for {comp}")
                return False

        # Validate scalable components
        required_scalable = ['router', 'consumer', 'target_precision_30', 'target_precision_50', 'target_precision_100']
        for comp in required_scalable:
            if comp not in profiles['scalable_components']:
                print(f"   ✗ Missing '{comp}' in scalable_components")
                return False
            if 'idle_watts' not in profiles['scalable_components'][comp]:
                print(f"   ✗ Missing 'idle_watts' for {comp}")
                return False
            if 'active_watts' not in profiles['scalable_components'][comp]:
                print(f"   ✗ Missing 'active_watts' for {comp}")
                return False

        print(f"   ✓ power_profiles.json")
        return True

    except json.JSONDecodeError as e:
        print(f"   ✗ Invalid JSON in power_profiles.json: {e}")
        return False
    except Exception as e:
        print(f"   ✗ Error validating power_profiles.json: {e}")
        return False

def main():
    """Run all checks."""
    print("=" * 60)
    print("Temporal Benchmark Pre-flight Check")
    print("=" * 60)

    all_ok = True

    # Check CLI tools
    print("\n1. Checking CLI tools...")
    for cmd in ["kubectl", "python3", "locust"]:
        if check_command(cmd):
            print(f"   ✓ {cmd}")
        else:
            print(f"   ✗ {cmd} NOT FOUND")
            all_ok = False

    # Check Python packages
    print("\n2. Checking Python packages...")
    for pkg in ["requests", "prometheus_client", "flask", "locust", "matplotlib"]:
        if check_python_package(pkg):
            print(f"   ✓ {pkg}")
        else:
            print(f"   ✗ {pkg} NOT INSTALLED")
            all_ok = False

    if not all_ok:
        print("\n   Install missing packages with:")
        print("   pip3 install --break-system-packages requests prometheus_client flask locust matplotlib")

    # Check files
    print("\n3. Checking experiment files...")
    base = Path(__file__).parent
    required_files = [
        "carbon_scenario.json",
        "locust_router.py",
        "run_temporal_benchmark.py",
        "setup_portforwards.sh",
        "plot_results.py",
    ]
    for fname in required_files:
        fpath = base / fname
        if fpath.exists():
            print(f"   ✓ {fname}")
        else:
            print(f"   ✗ {fname} NOT FOUND")
            all_ok = False

    # Check weight evaluation files
    print("\n3b. Checking weight evaluation files...")
    weight_eval_files = [
        ("power_profiles.json", check_power_profiles),
        ("power_calculator.py", None),
        ("weight_evaluation.py", None),
        ("plot_weight_evaluation.py", None),
    ]

    for fname, validator_func in weight_eval_files:
        fpath = base / fname
        if fpath.exists():
            if validator_func:
                if not validator_func():
                    all_ok = False
            else:
                print(f"   ✓ {fname}")
        else:
            print(f"   ✗ {fname} NOT FOUND (needed for weight evaluation)")
            all_ok = False
    
    # Check Kubernetes resources
    print("\n4. Checking Kubernetes resources...")
    resources = [
        ("trafficschedule", "traffic-schedule", "carbonstat"),
        ("deployment", "carbonrouter-decision-engine", "carbonrouter-system"),
        ("deployment", "buffer-service-router-traffic-schedule", "carbonstat"),
        ("deployment", "buffer-service-consumer-traffic-schedule", "carbonstat"),
    ]
    for kind, name, ns in resources:
        if check_kubernetes_resource(kind, name, ns):
            print(f"   ✓ {kind}/{name} in {ns}")
        else:
            print(f"   ✗ {kind}/{name} in {ns} NOT FOUND")
            all_ok = False
    
    # Check port-forwards
    print("\n5. Checking port-forwards...")
    ports = [
        (18000, "Router endpoint"),
        (18001, "Router metrics"),
        (18002, "Consumer metrics"),
        (18003, "Engine metrics"),
    ]
    
    all_ports_ok = True
    for port, desc in ports:
        if check_port_forward(port, desc):
            print(f"   ✓ localhost:{port} - {desc}")
        else:
            print(f"   ✗ localhost:{port} - {desc} NOT RESPONDING")
            all_ports_ok = False
    
    if not all_ports_ok:
        print("\n   Start port-forwards with:")
        print("   ./setup_portforwards.sh")
    
    # Check mock carbon API
    print("\n6. Checking mock carbon API...")
    try:
        # Check if mock API is running
        response = requests.get("http://localhost:5000/intensity/2024-01-01T00:00:00Z/fw48h", timeout=2)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✓ Mock API responding (got {len(data)} forecast points)")
        else:
            print(f"   ✗ Mock API returned status {response.status_code}")
            all_ok = False
    except Exception as e:
        print(f"   ✗ Mock API not responding: {e}")
        print("\n   Start mock API with:")
        print("   cd ../tests && python3 mock-carbon-api.py --step-minutes 1 --data ../experiments/carbon_scenario.json")
        all_ok = False
    
    # Check decision engine configuration
    print("\n7. Checking decision engine configuration...")
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployment", "carbonrouter-decision-engine", 
             "-n", "carbonrouter-system", "-o", "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='CARBON_API_URL')].value}"],
            capture_output=True,
            text=True,
            check=False
        )
        carbon_url = result.stdout.strip()
        if carbon_url:
            print(f"   ✓ CARBON_API_URL configured: {carbon_url}")
            if "5000" not in carbon_url and "host.docker.internal" not in carbon_url:
                print("   ⚠ Warning: URL doesn't point to mock API (localhost:5000)")
        else:
            print("   ✗ CARBON_API_URL not configured")
            print("\n   Configure with:")
            print("   kubectl set env deployment/carbonrouter-decision-engine -n carbonrouter-system CARBON_API_URL=http://host.docker.internal:5000")
            all_ok = False
    except Exception as e:
        print(f"   ✗ Could not check configuration: {e}")
        all_ok = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_ok:
        print("✅ All checks passed! Ready to run benchmark.")
        print("\nNext steps:")
        print("  # Run all policies (40 minutes)")
        print("  python3 run_temporal_benchmark.py")
        print("")
        print("  # Or run a single policy (10 minutes)")
        print("  python3 run_temporal_benchmark.py --policy credit-greedy")
        print("")
        print("  # Run weight evaluation (20 minutes)")
        print("  python3 weight_evaluation.py")
        print("")
        print("  # Generate graphs")
        print("  python3 plot_results.py")
        print("  python3 plot_weight_evaluation.py results/weight_evaluation_<timestamp>")
    else:
        print("❌ Some checks failed. Fix the issues above before running the benchmark.")
    print("=" * 60)

    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
