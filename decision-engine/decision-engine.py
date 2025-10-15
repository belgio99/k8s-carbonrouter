"""
Decision Engine - Carbon-Aware Traffic Scheduling Service

This service manages carbon-aware scheduling decisions for Kubernetes workloads.
It provides a REST API to configure and retrieve traffic schedules that optimize
for carbon intensity and quality-of-service constraints.

Main components:
- SchedulerSession: Long-lived scheduler instances for specific TrafficSchedules
- SchedulerRegistry: Registry managing multiple scheduler sessions
- Flask API: REST endpoints for schedule retrieval and configuration
"""

import logging
import os
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from flask import Flask, jsonify, request
from prometheus_client import start_http_server

from scheduler import SchedulerEngine
from scheduler.models import SchedulerConfig, StrategyProfile, precision_key


logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO").upper())
LOGGER = logging.getLogger("decision-engine")

# Default namespace and name for TrafficSchedule resources
DEFAULT_NAMESPACE = os.getenv("DEFAULT_SCHEDULE_NAMESPACE", "default")
DEFAULT_NAME = os.getenv("DEFAULT_SCHEDULE_NAME", "default")

# Configuration keys that can be overridden via API
SCHEDULER_CONFIG_KEYS = {
    "targetError",      # Target quality error threshold
    "creditMin",        # Minimum credit balance
    "creditMax",        # Maximum credit balance
    "creditWindow",     # Smoothing window for credit calculations
    "policy",           # Scheduling policy name
    "validFor",         # Schedule validity duration in seconds
    "discoveryInterval",# Interval for strategy discovery
    "carbonTarget",     # Carbon intensity target
    "carbonTimeout",    # Timeout for carbon data fetching
    "carbonCacheTTL",   # TTL for cached carbon data
}


def _partition_payload(
    payload: Optional[Mapping[str, Any]]
) -> tuple[Dict[str, Any], Dict[str, Dict[str, int]], Optional[List[StrategyProfile]]]:
    """
    Parse incoming configuration payload into its constituent parts.
    
    Args:
        payload: Raw configuration data from API request
        
    Returns:
        Tuple of (config_overrides, component_bounds, strategies):
        - config_overrides: Scheduler configuration parameters
        - component_bounds: Min/max replica constraints per component
        - strategies: List of precision strategies to use
    """
    if not payload or not isinstance(payload, Mapping):
        return {}, {}, None

    # Extract configuration section (can be nested under "scheduler" key)
    config_section: Mapping[str, Any]
    scheduler_section = payload.get("scheduler")
    if isinstance(scheduler_section, Mapping):
        config_section = scheduler_section
    else:
        config_section = payload

    # Extract valid configuration overrides
    config_overrides: Dict[str, Any] = {}
    for key in SCHEDULER_CONFIG_KEYS:
        if key in config_section and config_section[key] is not None:
            config_overrides[key] = config_section[key]

    # Parse component scaling bounds (min/max replicas)
    components_raw = payload.get("components")
    component_bounds = _normalise_component_bounds(components_raw)

    # Parse precision strategies if provided
    strategies: Optional[List[StrategyProfile]] = None
    if "strategies" in payload:
        strategies = _parse_strategies(payload.get("strategies"))

    return config_overrides, component_bounds, strategies


def _normalise_component_bounds(data: Any) -> Dict[str, Dict[str, int]]:
    """
    Extract and normalize component replica bounds from configuration.
    
    Args:
        data: Raw component bounds data (e.g., {"router": {"minReplicas": 1, "maxReplicas": 10}})
        
    Returns:
        Dictionary mapping component names to {"min": X, "max": Y} bounds
    """
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


def _as_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float, returning default on error."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_strategies(data: Any) -> List[StrategyProfile]:
    """
    Parse strategy profiles from configuration payload.
    
    Each strategy represents a precision/quality level (e.g., 0.3, 0.5, 1.0)
    with associated carbon intensity and annotations from deployment labels.
    
    Args:
        data: List of strategy dictionaries with precision, carbonIntensity, etc.
        
    Returns:
        List of StrategyProfile objects with normalized precision values (0.0-1.0)
    """
    if not isinstance(data, list):
        return []

    strategies: List[StrategyProfile] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue

        # Parse and normalize precision value to 0.0-1.0 range
        precision = _as_float(item.get("precision"), default=1.0)
        if precision > 1.0:  # Convert percentage (e.g., 30) to fraction (0.3)
            precision /= 100.0
        precision = max(0.0, min(precision, 1.0))  # Clamp to valid range

        # Generate standard strategy name (e.g., "precision-30")
        strategy_name = precision_key(precision)

        # Parse carbon intensity for this strategy
        carbon_intensity = _as_float(item.get("carbonIntensity"), default=0.0)

        # Check if strategy is enabled (default: True)
        enabled_raw = item.get("enabled")
        enabled = bool(enabled_raw) if enabled_raw is not None else True

        # Extract annotations (e.g., deployment labels)
        annotations_raw = item.get("annotations")
        annotations: Dict[str, str] = {}
        if isinstance(annotations_raw, Mapping):
            annotations = {
                str(key): str(value)
                for key, value in annotations_raw.items()
                if key is not None and value is not None
            }

        strategies.append(
            StrategyProfile(
                name=str(strategy_name),
                precision=precision,
                carbon_intensity=carbon_intensity,
                enabled=enabled,
                annotations=annotations,
            )
        )

    return strategies


def _as_int(value: Any) -> Optional[int]:
    """Safely convert value to int, returning None on error."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ScheduleNotReady(RuntimeError):
    """
    Exception raised when a schedule has not been computed yet.
    
    This is returned as HTTP 202 Accepted to indicate the schedule
    is being computed but not yet available.
    """

    def __init__(self, namespace: str, name: str) -> None:
        super().__init__(f"schedule {namespace}/{name} is not ready")
        self.namespace = namespace
        self.name = name


def _build_engine(
    namespace: str,
    name: str,
    config_overrides: Optional[Dict[str, Any]] = None,
    component_bounds: Optional[Dict[str, Dict[str, int]]] = None,
    strategies: Optional[List[StrategyProfile]] = None,
) -> SchedulerEngine:
    """
    Create a SchedulerEngine instance with given configuration.
    
    Args:
        namespace: Kubernetes namespace of the TrafficSchedule
        name: Name of the TrafficSchedule resource
        config_overrides: Optional configuration parameter overrides
        component_bounds: Optional min/max replica constraints
        strategies: Optional list of precision strategies to use
        
    Returns:
        Configured SchedulerEngine instance
    """
    config = SchedulerConfig.from_env()
    if config_overrides:
        config.apply_overrides(config_overrides)
    return SchedulerEngine(
        config=config,
        namespace=namespace,
        name=name,
        component_bounds=component_bounds,
        strategies=strategies,
    )


class SchedulerSession:
    """
    Manages a long-lived scheduler engine for a specific TrafficSchedule.
    
    Each session runs in its own background thread, periodically evaluating
    the optimal traffic distribution based on carbon intensity forecasts,
    quality constraints, and credit ledger state.
    
    Supports:
    - Automatic schedule refresh based on validity period
    - Manual schedule overrides with TTL
    - Dynamic configuration updates
    - Thread-safe schedule retrieval
    """

    def __init__(self, namespace: str, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize a new scheduler session.
        
        Args:
            namespace: Kubernetes namespace of the TrafficSchedule
            name: Name of the TrafficSchedule resource
            payload: Optional initial configuration payload
        """
        self.namespace = namespace
        self.name = name
        
        # Thread synchronization primitives
        self._lock = threading.RLock()
        self._refresh_event = threading.Event()  # Signals schedule refresh needed
        self._stop_event = threading.Event()     # Signals shutdown
        
        # Parse initial configuration
        config_overrides, component_bounds, strategies = _partition_payload(payload)
        self._strategies: Optional[List[StrategyProfile]] = (
            list(strategies) if strategies is not None else None
        )
        
        # Create scheduler engine
        self._engine = _build_engine(
            namespace,
            name,
            config_overrides,
            component_bounds,
            strategies=self._strategies,
        )
        self._config_overrides = dict(config_overrides)
        self._component_bounds = component_bounds
        
        # Schedule state
        self._manual_schedule: Optional[Dict[str, Any]] = None  # Manual override
        self._manual_expiry = 0.0                                # When manual override expires
        self._schedule: Optional[Dict[str, Any]] = None          # Current schedule
        
        # Start background scheduler thread
        self._thread = threading.Thread(
            target=self._run,
            name=f"scheduler[{namespace}/{name}]",
            daemon=True,
        )
        self._thread.start()
        self._refresh_event.set()  # Trigger initial evaluation

    def apply_overrides(self, payload: Dict[str, Any]) -> None:
        """
        Apply configuration overrides and rebuild the scheduler engine.
        
        This clears any manual overrides and invalidates the current schedule,
        forcing a fresh evaluation with the new configuration.
        
        Args:
            payload: Configuration payload with overrides
        """
        LOGGER.info("Applying overrides for %s/%s: %s", self.namespace, self.name, payload)
        config_overrides, component_bounds, strategies = _partition_payload(payload)
        
        # Use new strategies if provided, otherwise keep existing ones
        next_strategies: Optional[List[StrategyProfile]]
        if strategies is not None:
            next_strategies = list(strategies)
        else:
            next_strategies = self._strategies

        # Rebuild engine with new configuration
        engine = _build_engine(
            self.namespace,
            self.name,
            config_overrides,
            component_bounds,
            strategies=next_strategies,
        )
        
        # Update state atomically
        with self._lock:
            self._engine = engine
            self._config_overrides = dict(config_overrides)
            self._component_bounds = component_bounds
            self._strategies = next_strategies
            self._manual_schedule = None  # Clear manual override
            self._manual_expiry = 0.0
            self._schedule = None         # Invalidate current schedule
        self._refresh_event.set()  # Trigger immediate refresh

    def get_schedule(self) -> Optional[Dict[str, Any]]:
        """
        Get the current schedule, preferring manual overrides if active.
        
        Returns:
            Current schedule dictionary, or None if not yet computed
        """
        with self._lock:
            now = time.time()
            # Return manual override if still valid
            if self._manual_schedule and self._manual_expiry > now:
                return dict(self._manual_schedule)
            if self._schedule is None:
                return None
            return dict(self._schedule)

    def set_manual_override(self, payload: Dict[str, Any]) -> None:
        """
        Set a manual schedule override with TTL.
        
        The override will expire after the configured validity period,
        after which automatic scheduling resumes.
        
        Args:
            payload: Manual schedule data to use
        """
        with self._lock:
            ttl = max(1, int(self._engine.config.valid_for))
            self._manual_schedule = dict(payload)
            self._manual_expiry = time.time() + ttl
            self._schedule = dict(payload)
        self._refresh_event.set()

    def request_refresh(self) -> None:
        """Request an immediate schedule refresh."""
        self._refresh_event.set()

    def _run(self) -> None:
        """
        Main scheduler loop - runs in background thread.
        
        Periodically evaluates the optimal schedule based on:
        - Carbon intensity forecasts
        - Quality-of-service constraints (credit ledger)
        - Configured scheduling policy
        
        Respects manual overrides and handles evaluation failures gracefully.
        """
        backoff = 5  # Error backoff in seconds
        
        while not self._stop_event.is_set():
            # Wait for next refresh or timeout
            wait_seconds = self._next_wait()
            self._refresh_event.wait(timeout=wait_seconds)
            self._refresh_event.clear()

            if self._stop_event.is_set():
                break

            # Check if manual override is active
            with self._lock:
                engine = self._engine
                manual_active = self._manual_schedule is not None and self._manual_expiry > time.time()

            if manual_active:
                continue  # Skip evaluation while manual override is active

            # Evaluate schedule
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

            # Update schedule atomically
            with self._lock:
                self._schedule = schedule
                self._manual_schedule = None
                self._manual_expiry = 0.0

    def _next_wait(self) -> float:
        """
        Calculate how long to wait before next schedule refresh.
        
        Uses 80% of the validity period to ensure schedule is refreshed
        before it expires.
        
        Returns:
            Wait time in seconds
        """
        with self._lock:
            interval = max(1, int(self._engine.config.valid_for * 0.8))
        return interval

    def shutdown(self) -> None:
        """Gracefully shutdown the scheduler session and background thread."""
        self._stop_event.set()
        self._refresh_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


class SchedulerRegistry:
    """
    Registry of scheduler sessions keyed by namespace/name.
    
    Manages the lifecycle of SchedulerSession instances, ensuring only
    one session exists per TrafficSchedule resource. Provides thread-safe
    access to sessions for schedule retrieval and configuration updates.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._lock = threading.RLock()
        self._sessions: Dict[Tuple[str, str], SchedulerSession] = {}

    def configure(self, namespace: str, name: str, payload: Dict[str, Any]) -> None:
        """
        Configure or reconfigure a scheduler session.
        
        Creates the session if it doesn't exist, otherwise applies the
        configuration overrides to the existing session.
        
        Args:
            namespace: Kubernetes namespace
            name: TrafficSchedule name
            payload: Configuration payload
        """
        session = self._ensure_session(namespace, name, payload if payload else None)
        if payload:
            session.apply_overrides(payload)
        else:
            session.request_refresh()

    def get_schedule(self, namespace: str, name: str) -> Dict[str, Any]:
        """
        Get the current schedule for a TrafficSchedule.
        
        Args:
            namespace: Kubernetes namespace
            name: TrafficSchedule name
            
        Returns:
            Current schedule dictionary
            
        Raises:
            KeyError: If no session exists for this namespace/name
            ScheduleNotReady: If schedule has not been computed yet
        """
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
        """
        Set a manual schedule override for a TrafficSchedule.
        
        Args:
            namespace: Kubernetes namespace
            name: TrafficSchedule name
            payload: Manual schedule data
        """
        session = self._ensure_session(namespace, name)
        session.set_manual_override(payload)

    def ensure_default(self) -> SchedulerSession:
        """
        Ensure the default scheduler session exists.
        
        Returns:
            The default SchedulerSession instance
        """
        return self._ensure_session(DEFAULT_NAMESPACE, DEFAULT_NAME)

    def _ensure_session(
        self,
        namespace: str,
        name: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> SchedulerSession:
        """
        Get or create a scheduler session.
        
        Args:
            namespace: Kubernetes namespace
            name: TrafficSchedule name
            payload: Optional initial configuration
            
        Returns:
            Existing or newly created SchedulerSession
        """
        key = (namespace, name)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                LOGGER.info("Creating scheduler session for %s/%s", namespace, name)
                session = SchedulerSession(namespace, name, payload)
                self._sessions[key] = session
        return session


# Flask application and global registry
app = Flask(__name__)
registry = SchedulerRegistry()


# ============================================================================
# REST API Endpoints
# ============================================================================

@app.route("/schedule")
def get_default_schedule() -> Any:
    """
    Get the schedule for the default TrafficSchedule.
    
    Returns:
        200: Schedule JSON
        202: Schedule pending (being computed)
    """
    try:
        registry.ensure_default()
        schedule = registry.get_schedule(DEFAULT_NAMESPACE, DEFAULT_NAME)
    except ScheduleNotReady:
        return jsonify({"status": "pending"}), 202
    return jsonify(schedule)


@app.route("/schedule/<namespace>/<name>")
def get_schedule(namespace: str, name: str) -> Any:
    """
    Get the schedule for a specific TrafficSchedule.
    
    Args:
        namespace: Kubernetes namespace
        name: TrafficSchedule name
        
    Returns:
        200: Schedule JSON
        202: Schedule pending (being computed)
        404: Schedule not found (no configuration pushed yet)
    """
    try:
        schedule = registry.get_schedule(namespace, name)
    except KeyError:
        return jsonify({"error": f"unknown schedule {namespace}/{name}"}), 404
    except ScheduleNotReady:
        return jsonify({"status": "pending"}), 202
    return jsonify(schedule)


@app.route("/setschedule", methods=["POST"])
def set_default_manual_schedule() -> Any:
    """
    Set a manual schedule override for the default TrafficSchedule.
    
    The override will expire after the configured validity period.
    
    Request body: Schedule JSON
    
    Returns:
        202: Override accepted
        400: Invalid payload
    """
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
    """
    Set a manual schedule override for a specific TrafficSchedule.
    
    The override will expire after the configured validity period.
    
    Args:
        namespace: Kubernetes namespace
        name: TrafficSchedule name
        
    Request body: Schedule JSON
    
    Returns:
        202: Override accepted
        400: Invalid payload
    """
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be an object"}), 400
    registry.manual_override(namespace, name, data)
    LOGGER.warning("Manual override applied for %s/%s", namespace, name)
    return jsonify({"status": "schedule set"}), 202


@app.route("/config/<namespace>/<name>", methods=["PUT"])
def configure_schedule(namespace: str, name: str) -> Any:
    """
    Configure or reconfigure a TrafficSchedule.
    
    This is typically called by the Kubernetes operator when a TrafficSchedule
    resource is created or updated. The payload includes:
    - Scheduler configuration overrides
    - Component replica bounds
    - Precision strategies discovered from deployments
    
    Args:
        namespace: Kubernetes namespace
        name: TrafficSchedule name
        
    Request body: Configuration JSON
    
    Returns:
        202: Configuration accepted
        400: Invalid payload
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be an object"}), 400
    registry.configure(namespace, name, payload)
    return jsonify({"status": "accepted"}), 202


@app.route("/healthz")
def health() -> Any:
    """
    Health check endpoint.
    
    Returns:
        200: Service is ready
    """
    return jsonify({"status": "ready"}), 200


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    # Start Prometheus metrics server
    metrics_port = int(os.getenv("METRICS_PORT", "8001"))
    LOGGER.info("Starting Prometheus metrics server on port %s", metrics_port)
    start_http_server(metrics_port)

    # Ensure default scheduler session exists
    registry.ensure_default()

    # Start Flask API server
    app.run(host="0.0.0.0", port=80)