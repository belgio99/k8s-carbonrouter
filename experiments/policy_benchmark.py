#!/usr/bin/env python3
"""Automated benchmark pipeline for comparing scheduler policies.

The script orchestrates a full experiment cycle for each policy:

1. Apply the policy to the TrafficSchedule via `kubectl patch`.
2. Reset the carbon-intensity scenario on the mock API.
3. Launch Locust in headless mode to generate HTTP load against the router.
4. Sample Prometheus metrics (router, consumer, decision engine) while the
   workload is running.
5. Persist raw artefacts and compute summary statistics per policy.

Outputs are stored under `experiments/results/<timestamp>` by default. Each
policy gets its own folder containing:

* `schedule_before.json` / `schedule_after.json`
* Raw metric snapshots before & after (`*_metrics.json`)
* `locust-*.csv` and `locust.log`
* `timeseries.csv` (per-interval request mix, precision, credits)
* `summary.json` (aggregated KPIs referenced in the thesis)

An aggregate `benchmark_summary.json` is produced at the root output folder.

Prerequisites: kubectl, locust, requests, prometheus_client, and access to the
cluster/port-forwards described in `experiments/README.md`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests  # type: ignore
from prometheus_client.parser import text_string_to_metric_families  # type: ignore

DEFAULT_POLICIES = [
    "credit-greedy",
    "forecast-aware",
    "forecast-aware-global",
    "precision-tier",
]


@dataclass
class MetricSnapshot:
    router: Dict[str, Any]
    consumer: Dict[str, Any]
    engine: Dict[str, Any]


@dataclass
class FlavourMeta:
    precision: float
    carbon_intensity: Optional[float]


def run_cmd(argv: List[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the CompletedProcess."""

    result = subprocess.run(
        argv,
        check=check,
        capture_output=capture,
        text=True,
    )
    return result


def patch_policy(namespace: str, schedule: str, policy: str) -> None:
    payload = json.dumps({"spec": {"scheduler": {"policy": policy}}})
    run_cmd(
        [
            "kubectl",
            "patch",
            "trafficschedule",
            schedule,
            "-n",
            namespace,
            "--type=merge",
            f"-p={payload}",
        ]
    )


def fetch_schedule(namespace: str, schedule: str) -> Dict[str, Any]:
    result = run_cmd(
        [
            "kubectl",
            "get",
            "trafficschedule",
            schedule,
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )
    data = json.loads(result.stdout or "{}")
    return data.get("status", {})


def wait_for_policy(namespace: str, schedule: str, policy: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            spec_raw = run_cmd(
                [
                    "kubectl",
                    "get",
                    "trafficschedule",
                    schedule,
                    "-n",
                    namespace,
                    "-o",
                    "jsonpath={.spec.scheduler.policy}",
                ]
            )
            current = spec_raw.stdout.strip()
            if current == policy:
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(2)
    raise RuntimeError(f"policy {policy} did not apply within {timeout}s")


def scrape_metrics(url: str) -> Dict[str, List[Dict[str, Any]]]:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    metrics: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            metrics[family.name].append(
                {
                    "name": sample.name,
                    "labels": dict(sample.labels),
                    "value": float(sample.value),
                    "timestamp": sample.timestamp,
                }
            )
    return metrics


def collect_metrics(router_url: str, consumer_url: str, engine_url: str) -> MetricSnapshot:
    return MetricSnapshot(
        router=scrape_metrics(router_url),
        consumer=scrape_metrics(consumer_url),
        engine=scrape_metrics(engine_url),
    )


def resolve_flavours(schedule: Dict[str, Any]) -> Dict[str, FlavourMeta]:
    candidates = schedule.get("flavours") or schedule.get("flavourRules") or []
    result: Dict[str, FlavourMeta] = {}
    for entry in candidates:
        name = str(entry.get("name") or entry.get("flavourName") or "").strip()
        if not name:
            continue
        precision = entry.get("precision")
        if isinstance(precision, str):
            try:
                precision = float(precision)
            except ValueError:
                precision = None
        if precision is None:
            precision = 0.0
        if precision > 1.0:
            precision = precision / 100.0
        carbon = entry.get("carbonIntensity")
        try:
            carbon_value: Optional[float] = float(carbon) if carbon is not None else None
        except (TypeError, ValueError):
            carbon_value = None
        result[name] = FlavourMeta(precision=float(precision), carbon_intensity=carbon_value)
    return result


def _sum_series(samples: Iterable[Dict[str, Any]], metric: str, labels: Dict[str, str]) -> float:
    total = 0.0
    for sample in samples:
        if sample["name"] != metric:
            continue
        if all(sample["labels"].get(k) == v for k, v in labels.items()):
            total += sample["value"]
    return total


def extract_router_counts(metrics: Dict[str, List[Dict[str, Any]]]) -> Dict[str, float]:
    counts: Dict[str, float] = defaultdict(float)
    for sample in metrics.get("router_http_requests_total", []):
        if sample["labels"].get("qtype") != "queue":
            continue
        flavour = sample["labels"].get("flavour") or "unknown"
        counts[flavour] += sample["value"]
    return dict(counts)


def extract_histogram_mean(metrics: Dict[str, List[Dict[str, Any]]], base: str, flavour: str) -> Optional[float]:
    sum_value = None
    count_value = None
    for sample in metrics.get(f"{base}_sum", []):
        if sample["labels"].get("flavour") == flavour:
            sum_value = sample["value"]
            break
    for sample in metrics.get(f"{base}_count", []):
        if sample["labels"].get("flavour") == flavour:
            count_value = sample["value"]
            break
    if sum_value is None or count_value in (None, 0):
        return None
    return sum_value / count_value


def diff_counters(after: Dict[str, float], before: Dict[str, float]) -> Dict[str, float]:
    flavours = set(after) | set(before)
    return {flavour: after.get(flavour, 0.0) - before.get(flavour, 0.0) for flavour in flavours}


def safe_total(values: Dict[str, float]) -> float:
    return sum(v for v in values.values() if v > 0)


def extract_engine_value(
    metrics: Dict[str, List[Dict[str, Any]]],
    metric: str,
    namespace: str,
    schedule: str,
    policy: str,
) -> Optional[float]:
    for sample in metrics.get(metric, []):
        labels = sample["labels"]
        if (
            labels.get("namespace") == namespace
            and labels.get("schedule") == schedule
            and labels.get("policy") == policy
        ):
            return sample["value"]
    return None


def record_timeseries_row(
    writer: csv.writer,
    timestamp: float,
    counts: Dict[str, float],
    meta: Dict[str, FlavourMeta],
    credit_balance: Optional[float],
    avg_precision: Optional[float],
) -> None:
    total = safe_total(counts)
    if total <= 0:
        return
    precision = 0.0
    intensity = 0.0
    for flavour, count in counts.items():
        share = count / total if total else 0.0
        info = meta.get(flavour)
        if info:
            precision += share * info.precision
            if info.carbon_intensity is not None:
                intensity += share * info.carbon_intensity
    writer.writerow(
        [
            datetime.utcfromtimestamp(timestamp).isoformat() + "Z",
            total,
            precision if total else "",
            intensity if total else "",
            credit_balance if credit_balance is not None else "",
            avg_precision if avg_precision is not None else "",
        ]
    )


def compute_summary(
    policy: str,
    duration_sec: float,
    total_requests: float,
    counts: Dict[str, float],
    meta: Dict[str, FlavourMeta],
    credit_balance: Optional[float],
    credit_velocity: Optional[float],
    policy_choices_before: float,
    policy_choices_after: float,
) -> Dict[str, Any]:
    precision_num = 0.0
    carbon_num = 0.0
    for flavour, count in counts.items():
        info = meta.get(flavour)
        if not info:
            continue
        precision_num += count * info.precision
        if info.carbon_intensity is not None:
            carbon_num += count * info.carbon_intensity
    mean_precision = precision_num / total_requests if total_requests else 0.0
    mean_carbon = carbon_num / total_requests if carbon_num and total_requests else None
    return {
        "policy": policy,
        "duration_seconds": duration_sec,
        "total_requests": total_requests,
        "requests_by_flavour": counts,
        "mean_precision": mean_precision,
        "mean_carbon_intensity": mean_carbon,
        "credit_balance_final": credit_balance,
        "credit_velocity_final": credit_velocity,
        "policy_evaluations": policy_choices_after - policy_choices_before,
    }


def start_locust(
    locustfile: Path,
    policy_dir: Path,
    host: str,
    users: int,
    spawn_rate: float,
    duration: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    policy_dir.mkdir(parents=True, exist_ok=True)
    logfile = policy_dir / "locust.log"
    base_cmd = [
        "locust",
        "-f",
        str(locustfile),
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "-t",
        duration,
        "--host",
        host,
        "--csv",
        str(policy_dir / "locust"),
        "--only-summary",
        "--stop-timeout",
        "20",
    ]
    overrides = {k: v for k, v in (extra_env or {}).items()}
    env = os.environ.copy()
    env.update(overrides)
    log_handle = open(logfile, "w", encoding="utf-8")
    proc = subprocess.Popen(
        base_cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    setattr(proc, "_benchmark_log_handle", log_handle)
    return proc


def ensure_scenario(mock_url: str, pattern: List[int]) -> None:
    payload = {"scenario": "custom", "pattern": pattern}
    response = requests.post(f"{mock_url.rstrip('/')}/scenario", json=payload, timeout=10)
    response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark scheduler policies")
    parser.add_argument("--namespace", default="carbonstat")
    parser.add_argument("--schedule", default="traffic-schedule")
    parser.add_argument("--policies", nargs="*", default=DEFAULT_POLICIES)
    parser.add_argument("--router-url", required=True, help="Base URL for router HTTP endpoint")
    parser.add_argument("--router-metrics", required=True, help="Prometheus metrics URL for router")
    parser.add_argument("--consumer-metrics", required=True, help="Prometheus metrics URL for consumer")
    parser.add_argument("--engine-metrics", required=True, help="Prometheus metrics URL for decision engine")
    parser.add_argument("--decision-engine", default="http://127.0.0.1:8080")
    parser.add_argument("--mock-carbon", help="Mock carbon API base URL (for scenario reset)")
    parser.add_argument("--scenario", default=str(Path(__file__).with_name("carbon_scenario.json")))
    parser.add_argument("--duration", default="10m", help="Locust run duration (e.g. 10m, 600s)")
    parser.add_argument("--users", type=int, default=180)
    parser.add_argument("--spawn-rate", type=float, default=30.0)
    parser.add_argument("--sample-interval", type=float, default=30.0)
    parser.add_argument("--locustfile", default=str(Path(__file__).with_name("locust_router.py")))
    parser.add_argument("--output-dir", help="Custom output directory")
    parser.add_argument("--settle-seconds", type=int, default=15, help="Wait time after policy switch")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat whole policy list N times")
    parser.add_argument("--extra-env", default="", help="Additional env vars for Locust (KEY=VALUE, comma-separated)")
    return parser.parse_args()


def parse_env_pairs(raw: str) -> Dict[str, str]:
    if not raw:
        return {}
    output: Dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        output[key.strip()] = value.strip()
    return output


def load_pattern(path: str) -> List[int]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        pattern = payload.get("pattern")
    else:
        pattern = payload
    if not isinstance(pattern, list):
        raise ValueError("scenario JSON must contain a list under 'pattern'")
    return [int(x) for x in pattern]


def main() -> None:
    args = parse_args()
    pattern = load_pattern(args.scenario)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_dir or Path(__file__).parent / "results" / timestamp)
    output_root.mkdir(parents=True, exist_ok=True)
    env_overrides = parse_env_pairs(args.extra_env)

    aggregate: List[Dict[str, Any]] = []

    policies = list(args.policies) * max(1, args.repeat)

    for policy in policies:
        policy_dir = output_root / policy.replace("/", "-")
        policy_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== Running policy: {policy} ===")

        print("→ Applying policy via kubectl patch…")
        patch_policy(args.namespace, args.schedule, policy)
        wait_for_policy(args.namespace, args.schedule, policy)

        if args.mock_carbon:
            print("→ Resetting mock carbon scenario…")
            ensure_scenario(args.mock_carbon, pattern)

        print(f"→ Waiting {args.settle_seconds}s for scheduler to stabilise…")
        time.sleep(args.settle_seconds)

        schedule_before = fetch_schedule(args.namespace, args.schedule)
        (policy_dir / "schedule_before.json").write_text(json.dumps(schedule_before, indent=2))
        flavour_meta = resolve_flavours(schedule_before)

        print("→ Collecting baseline metrics…")
        baseline = collect_metrics(args.router_metrics, args.consumer_metrics, args.engine_metrics)
        (policy_dir / "metrics_before_router.json").write_text(json.dumps(baseline.router, indent=2))
        (policy_dir / "metrics_before_consumer.json").write_text(json.dumps(baseline.consumer, indent=2))
        (policy_dir / "metrics_before_engine.json").write_text(json.dumps(baseline.engine, indent=2))
        router_counts_before = extract_router_counts(baseline.router)
        policy_choices_before = _sum_series(
            baseline.engine.get("scheduler_policy_choice_total", []),
            "scheduler_policy_choice_total",
            {"policy": policy, "namespace": args.namespace, "schedule": args.schedule},
        )

        locust_proc = start_locust(
            Path(args.locustfile),
            policy_dir,
            args.router_url,
            args.users,
            args.spawn_rate,
            args.duration,
            env_overrides,
        )

        timeseries_path = policy_dir / "timeseries.csv"
        with timeseries_path.open("w", encoding="utf-8", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                "timestamp",
                "delta_requests",
                "mean_precision",
                "mean_carbon_intensity",
                "credit_balance",
                "engine_reported_precision",
            ])

            last_counts = router_counts_before
            next_sample = time.time()
            start_time = next_sample
            while True:
                now = time.time()
                if now >= next_sample or locust_proc.poll() is not None:
                    router_metrics = scrape_metrics(args.router_metrics)
                    engine_metrics = scrape_metrics(args.engine_metrics)
                    counts = extract_router_counts(router_metrics)
                    delta = diff_counters(counts, last_counts)
                    credit_balance = extract_engine_value(
                        engine_metrics,
                        "scheduler_credit_balance",
                        args.namespace,
                        args.schedule,
                        policy,
                    )
                    engine_precision = extract_engine_value(
                        engine_metrics,
                        "scheduler_avg_precision",
                        args.namespace,
                        args.schedule,
                        policy,
                    )
                    record_timeseries_row(writer, now, delta, flavour_meta, credit_balance, engine_precision)
                    last_counts = counts
                    next_sample = now + args.sample_interval
                if locust_proc.poll() is not None:
                    break
                time.sleep(1)

            locust_proc.wait()
            if locust_proc.returncode not in (0, None):
                raise RuntimeError(f"locust exited with status {locust_proc.returncode}")
            end_time = time.time()

        log_handle = getattr(locust_proc, "_benchmark_log_handle", None)
        if log_handle:
            log_handle.close()

        print("→ Collecting final metrics…")
        final_metrics = collect_metrics(args.router_metrics, args.consumer_metrics, args.engine_metrics)
        (policy_dir / "metrics_after_router.json").write_text(json.dumps(final_metrics.router, indent=2))
        (policy_dir / "metrics_after_consumer.json").write_text(json.dumps(final_metrics.consumer, indent=2))
        (policy_dir / "metrics_after_engine.json").write_text(json.dumps(final_metrics.engine, indent=2))

        schedule_after = fetch_schedule(args.namespace, args.schedule)
        (policy_dir / "schedule_after.json").write_text(json.dumps(schedule_after, indent=2))

        router_counts_after = extract_router_counts(final_metrics.router)
        counts_delta = diff_counters(router_counts_after, router_counts_before)
        total_requests = safe_total(counts_delta)

        credit_balance = extract_engine_value(
            final_metrics.engine,
            "scheduler_credit_balance",
            args.namespace,
            args.schedule,
            policy,
        )
        credit_velocity = extract_engine_value(
            final_metrics.engine,
            "scheduler_credit_velocity",
            args.namespace,
            args.schedule,
            policy,
        )
        policy_choices_after = _sum_series(
            final_metrics.engine.get("scheduler_policy_choice_total", []),
            "scheduler_policy_choice_total",
            {"policy": policy, "namespace": args.namespace, "schedule": args.schedule},
        )

        summary = compute_summary(
            policy=policy,
            duration_sec=end_time - start_time,
            total_requests=total_requests,
            counts=counts_delta,
            meta=flavour_meta,
            credit_balance=credit_balance,
            credit_velocity=credit_velocity,
            policy_choices_before=policy_choices_before,
            policy_choices_after=policy_choices_after,
        )
        (policy_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        aggregate.append(summary)
        print(f"→ Completed policy {policy}: processed {int(total_requests)} requests")

    (output_root / "benchmark_summary.json").write_text(json.dumps(aggregate, indent=2))
    print("\n=== Benchmark complete ===")
    print(f"Results folder: {output_root}")


if __name__ == "__main__":
    main()
