"""Locust ramping load generator for autoscaling benchmark.

This load generator creates a ramping spike pattern to stress-test the
carbon-aware autoscaling throttling mechanism:

- 0-2 min: Ramp from 50 to 250 users (spike during high carbon period)
- 2-5 min: Hold at 250 users (sustained pressure during transition)
- 5-10 min: Drop to 100 users (steady state during low carbon period)

The pattern is designed to test whether the system can:
1. Avoid aggressive scaling during high carbon by throttling and queuing
2. Process accumulated queues during low carbon periods
3. Demonstrate carbon savings through temporal shifting
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict

from locust import HttpUser, LoadTestShape, between, task  # type: ignore

DEFAULT_NUMBERS = [1, 2, 3, 50, 500, 1000]


def _load_payload() -> Dict[str, Any]:
    raw = os.getenv("BENCHMARK_PAYLOAD")
    if not raw:
        return {"numbers": list(DEFAULT_NUMBERS)}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"numbers": list(DEFAULT_NUMBERS)}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"numbers": payload}
    return {"numbers": list(DEFAULT_NUMBERS)}


def _load_headers() -> Dict[str, str]:
    headers = {"x-tenant": "benchmark"}
    raw = os.getenv("BENCHMARK_HEADERS")
    if not raw:
        return headers
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        return headers
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})
    return headers


PAYLOAD = _load_payload()
HEADERS = _load_headers()
FORCED_FLAVOUR = os.getenv("BENCHMARK_FORCE_FLAVOUR")
URGENT_RATIO = max(0.0, min(1.0, float(os.getenv("BENCHMARK_URGENT_RATIO", "0"))))
REQUEST_TIMEOUT = float(os.getenv("BENCHMARK_TIMEOUT", "60"))
BENCHMARK_PATH = os.getenv("BENCHMARK_PATH", "/avg")
WAIT_MIN = float(os.getenv("BENCHMARK_WAIT_MIN", "0.05"))
WAIT_MAX = float(os.getenv("BENCHMARK_WAIT_MAX", "0.15"))


class RouterBenchmarkUser(HttpUser):
    """User that generates requests to the router's /avg endpoint."""

    wait_time = between(WAIT_MIN, WAIT_MAX)

    @task
    def invoke_avg(self) -> None:
        headers = dict(HEADERS)
        if FORCED_FLAVOUR:
            headers["x-carbonrouter"] = FORCED_FLAVOUR
        if URGENT_RATIO > 0 and random.random() < URGENT_RATIO:
            headers["x-urgent"] = "true"
        self.client.post(
            BENCHMARK_PATH,
            json=PAYLOAD,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            name="avg",
        )


class RampingLoadShape(LoadTestShape):
    """Custom load shape for autoscaling benchmark.

    Load pattern:
    - 0-120s: Ramp from 50 to 250 users (linear increase, spike during high carbon)
    - 120-300s: Hold at 250 users (sustained pressure during transition)
    - 300-600s: Drop to 100 users (steady processing during low carbon)
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time."""
        run_time = self.get_run_time()

        if run_time < 120:
            # Phase 1: Ramp up from 50 to 250 over 2 minutes
            # Linear interpolation: 50 + (250-50) * (t/120)
            user_count = int(50 + (200 * run_time / 120))
            spawn_rate = 25  # Aggressive spawn rate
            return (user_count, spawn_rate)

        elif run_time < 300:
            # Phase 2: Hold at 250 users for 3 minutes (120-300s)
            return (250, 25)

        elif run_time < 600:
            # Phase 3: Drop to 100 users for remaining 5 minutes (300-600s)
            return (100, 25)

        else:
            # Test complete after 10 minutes
            return None
