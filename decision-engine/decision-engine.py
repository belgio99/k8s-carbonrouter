import logging
import os
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from flask import Flask, jsonify, request
from prometheus_client import start_http_server

from scheduler import SchedulerEngine
from scheduler.models import SchedulerConfig


logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO").upper())
LOGGER = logging.getLogger("decision-engine")

DEFAULT_NAMESPACE = os.getenv("DEFAULT_SCHEDULE_NAMESPACE", "default")
DEFAULT_NAME = os.getenv("DEFAULT_SCHEDULE_NAME", "default")

SCHEDULER_CONFIG_KEYS = {
    "targetError",
    "creditMin",
    "creditMax",
    "creditWindow",
    "policy",
    "validFor",
    "discoveryInterval",
    "carbonTarget",
    "carbonTimeout",
    "carbonCacheTTL",
}


def _partition_payload(payload: Optional[Mapping[str, Any]]) -> tuple[Dict[str, Any], Dict[str, Dict[str, int]]]:
    if not payload or not isinstance(payload, Mapping):
        return {}, {}

    config_section: Mapping[str, Any]
    scheduler_section = payload.get("scheduler")
    if isinstance(scheduler_section, Mapping):
        config_section = scheduler_section
    else:
        config_section = payload

    config_overrides: Dict[str, Any] = {}
    for key in SCHEDULER_CONFIG_KEYS:
        if key in config_section and config_section[key] is not None:
            config_overrides[key] = config_section[key]

    components_raw = payload.get("components")
    component_bounds = _normalise_component_bounds(components_raw)

    return config_overrides, component_bounds


def _normalise_component_bounds(data: Any) -> Dict[str, Dict[str, int]]:
    bounds: Dict[str, Dict[str, int]] = {}
    if not isinstance(data, Mapping):
        return bounds

    for component, settings in data.items():
        if not isinstance(component, str) or not isinstance(settings, Mapping):
            continue
        entries: Dict[str, int] = {}
        min_value = _as_int(settings.get("minReplicas"))
        max_value = _as_int(settings.get("maxReplicas"))
        if min_value is not None:
            entries["min"] = min_value
        if max_value is not None:
            entries["max"] = max_value
        if entries:
            bounds[component] = entries
    return bounds


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ScheduleNotReady(RuntimeError):
    """Raised when a schedule has not been computed yet."""

    def __init__(self, namespace: str, name: str) -> None:
        super().__init__(f"schedule {namespace}/{name} is not ready")
        self.namespace = namespace
        self.name = name


def _build_engine(
    namespace: str,
    name: str,
    config_overrides: Optional[Dict[str, Any]] = None,
    component_bounds: Optional[Dict[str, Dict[str, int]]] = None,
) -> SchedulerEngine:
    config = SchedulerConfig.from_env()
    if config_overrides:
        config.apply_overrides(config_overrides)
    return SchedulerEngine(
        config=config,
        namespace=namespace,
        name=name,
        component_bounds=component_bounds,
    )


class SchedulerSession:
    """Manages a long-lived scheduler engine for a specific TrafficSchedule."""

    def __init__(self, namespace: str, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.namespace = namespace
        self.name = name
        self._lock = threading.RLock()
        self._refresh_event = threading.Event()
        self._stop_event = threading.Event()
        config_overrides, component_bounds = _partition_payload(payload)
        self._engine = _build_engine(namespace, name, config_overrides, component_bounds)
        self._config_overrides = dict(config_overrides)
        self._component_bounds = component_bounds
        self._manual_schedule: Optional[Dict[str, Any]] = None
        self._manual_deadline = 0.0
        self._schedule: Optional[Dict[str, Any]] = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"scheduler[{namespace}/{name}]",
            daemon=True,
        )
        self._thread.start()
        self._refresh_event.set()

    def apply_overrides(self, payload: Dict[str, Any]) -> None:
        LOGGER.info("Applying overrides for %s/%s: %s", self.namespace, self.name, payload)
        config_overrides, component_bounds = _partition_payload(payload)
        engine = _build_engine(self.namespace, self.name, config_overrides, component_bounds)
        with self._lock:
            self._engine = engine
            self._config_overrides = dict(config_overrides)
            self._component_bounds = component_bounds
            self._manual_schedule = None
            self._manual_deadline = 0.0
            self._schedule = None
        self._refresh_event.set()

    def get_schedule(self) -> Dict[str, Any] | None:
        with self._lock:
            now = time.time()
            if self._manual_schedule and self._manual_deadline > now:
                return dict(self._manual_schedule)
            if self._schedule is None:
                return None
            return dict(self._schedule)

    def set_manual_override(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            ttl = max(1, int(self._engine.config.valid_for))
            self._manual_schedule = dict(payload)
            self._manual_deadline = time.time() + ttl
            self._schedule = dict(payload)
        self._refresh_event.set()

    def request_refresh(self) -> None:
        self._refresh_event.set()

    def _run(self) -> None:
        backoff = 5
        while not self._stop_event.is_set():
            wait_seconds = self._next_wait()
            self._refresh_event.wait(timeout=wait_seconds)
            self._refresh_event.clear()

            if self._stop_event.is_set():
                break

            with self._lock:
                engine = self._engine
                manual_active = (
                    self._manual_schedule is not None and self._manual_deadline > time.time()
                )

            if manual_active:
                continue

            try:
                decision = engine.evaluate()
                schedule = decision.as_dict()
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception(
                    "Scheduler iteration failed for %s/%s: %s",
                    self.namespace,
                    self.name,
                    exc,
                )
                time.sleep(backoff)
                continue

            with self._lock:
                self._schedule = schedule
                self._manual_schedule = None
                self._manual_deadline = 0.0

    def _next_wait(self) -> float:
        with self._lock:
            interval = max(1, int(self._engine.config.valid_for * 0.8))
        return interval

    def shutdown(self) -> None:
        self._stop_event.set()
        self._refresh_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


class SchedulerRegistry:
    """Registry of scheduler sessions keyed by namespace/name."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[Tuple[str, str], SchedulerSession] = {}

    def configure(self, namespace: str, name: str, payload: Dict[str, Any]) -> None:
        session = self._ensure_session(namespace, name, payload if payload else None)
        if payload:
            session.apply_overrides(payload)
        else:
            session.request_refresh()

    def get_schedule(self, namespace: str, name: str) -> Dict[str, Any]:
        key = (namespace, name)
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            raise KeyError(key)
        schedule = session.get_schedule()
        if schedule is None:
            raise ScheduleNotReady(namespace, name)
        return schedule

    def manual_override(self, namespace: str, name: str, payload: Dict[str, Any]) -> None:
        session = self._ensure_session(namespace, name)
        session.set_manual_override(payload)

    def ensure_default(self) -> SchedulerSession:
        return self._ensure_session(DEFAULT_NAMESPACE, DEFAULT_NAME)

    def _ensure_session(
        self,
        namespace: str,
        name: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> SchedulerSession:
        key = (namespace, name)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                LOGGER.info("Creating scheduler session for %s/%s", namespace, name)
                session = SchedulerSession(namespace, name, payload)
                self._sessions[key] = session
        return session


app = Flask(__name__)
registry = SchedulerRegistry()


@app.route("/schedule")
def get_default_schedule() -> Any:
    try:
        registry.ensure_default()
        schedule = registry.get_schedule(DEFAULT_NAMESPACE, DEFAULT_NAME)
    except ScheduleNotReady:
        return jsonify({"status": "pending"}), 202
    return jsonify(schedule)


@app.route("/schedule/<namespace>/<name>")
def get_schedule(namespace: str, name: str) -> Any:
    try:
        schedule = registry.get_schedule(namespace, name)
    except KeyError:
        return jsonify({"error": f"unknown schedule {namespace}/{name}"}), 404
    except ScheduleNotReady:
        return jsonify({"status": "pending"}), 202
    return jsonify(schedule)


@app.route("/setschedule", methods=["POST"])
def set_default_manual_schedule() -> Any:
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be an object"}), 400
    registry.manual_override(DEFAULT_NAMESPACE, DEFAULT_NAME, data)
    LOGGER.warning(
        "Manual schedule override applied for %s/%s",
        DEFAULT_NAMESPACE,
        DEFAULT_NAME,
    )
    return jsonify({"status": "schedule set"}), 202


@app.route("/schedule/<namespace>/<name>/manual", methods=["POST"])
def set_manual_schedule(namespace: str, name: str) -> Any:
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be an object"}), 400
    registry.manual_override(namespace, name, data)
    LOGGER.warning("Manual override applied for %s/%s", namespace, name)
    return jsonify({"status": "schedule set"}), 202


@app.route("/config/<namespace>/<name>", methods=["PUT"])
def configure_schedule(namespace: str, name: str) -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be an object"}), 400
    registry.configure(namespace, name, payload)
    return jsonify({"status": "accepted"}), 202


@app.route("/healthz")
def health() -> Any:
    return jsonify({"status": "ready"}), 200


if __name__ == "__main__":
    metrics_port = int(os.getenv("METRICS_PORT", "8001"))
    LOGGER.info("Starting Prometheus metrics server on port %s", metrics_port)
    start_http_server(metrics_port)

    registry.ensure_default()

    app.run(host="0.0.0.0", port=80)