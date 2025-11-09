"""Locust tasks for routing benchmark experiments.

The workload generates POST requests against the router's `/avg` endpoint,
mirroring the carbonstat service signature. Behaviour can be tweaked using
environment variables so the same locustfile works for different scenarios.

Configuration knobs (via env vars):

* `BENCHMARK_PATH` – request path (default `/avg`).
* `BENCHMARK_WAIT_MIN` / `BENCHMARK_WAIT_MAX` – inter-request wait window in
  seconds (defaults 0.05–0.15).
* `BENCHMARK_PAYLOAD` – JSON array or object used as request body. If an array
  is provided it becomes `{ "numbers": [...] }`.
* `BENCHMARK_HEADERS` – JSON object merged into the default headers.
* `BENCHMARK_FORCE_FLAVOUR` – if set, sends `x-carbonrouter` header with this
  value for every request.
* `BENCHMARK_URGENT_RATIO` – probability (0–1) of tagging a request as urgent
  via `x-urgent: true`.
* `BENCHMARK_TIMEOUT` – request timeout in seconds (default 10).
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict

from locust import HttpUser, between, task  # type: ignore

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
REQUEST_TIMEOUT = float(os.getenv("BENCHMARK_TIMEOUT", "10"))
BENCHMARK_PATH = os.getenv("BENCHMARK_PATH", "/avg")
WAIT_MIN = float(os.getenv("BENCHMARK_WAIT_MIN", "0.05"))
WAIT_MAX = float(os.getenv("BENCHMARK_WAIT_MAX", "0.15"))


class RouterBenchmarkUser(HttpUser):
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
