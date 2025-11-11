"""External data providers used by the scheduler."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

try:  # pragma: no cover - optional dependency safeguard for linters
    import requests  # type: ignore[import]
except ImportError:  # pragma: no cover - requests is an optional runtime dependency
    requests = None  # type: ignore[assignment]

from .models import ForecastPoint, ForecastSnapshot


class CarbonForecastProvider:
    """Fetch current and near-term carbon intensity schedule information."""

    _DEFAULT_BASE = "https://api.carbonintensity.org.uk"

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        cache_ttl: Optional[float] = None,
        target: Optional[str] = None,
    ) -> None:
        env_base = os.getenv("CARBON_API_URL")
        configured = base_url or env_base
        self.base_url = (configured or self._DEFAULT_BASE).rstrip("/")
        self._configured_base = configured
        timeout_val = timeout if timeout is not None else float(os.getenv("CARBON_API_TIMEOUT", "2.0"))
        cache_val = cache_ttl if cache_ttl is not None else float(os.getenv("CARBON_API_CACHE_TTL", "300.0"))
        target_val = target if target is not None else os.getenv("CARBON_API_TARGET", "national")
        self.timeout = float(timeout_val)
        self.cache_ttl = float(cache_val)
        self._target_type, self._target_value = self._parse_target(target_val)
        self._cache_lock = threading.Lock()
        self._cached_schedule: Optional[tuple[float, List[ForecastPoint]]] = None

    def fetch(self) -> ForecastSnapshot:
        if not self.base_url or requests is None:
            return ForecastSnapshot()

        schedule = self._load_schedule()
        if schedule:
            intensity_now = schedule[0].forecast
            intensity_next = schedule[1].forecast if len(schedule) > 1 else schedule[0].forecast
            index_now = schedule[0].index
            index_next = schedule[1].index if len(schedule) > 1 else schedule[0].index
            snapshot = ForecastSnapshot(
                intensity_now=intensity_now,
                intensity_next=intensity_next,
                index_now=index_now,
                index_next=index_next,
            )
            snapshot.schedule = schedule
            return snapshot

        if self._configured_base:
            legacy = self._fetch_legacy()
            if legacy:
                return legacy

        return ForecastSnapshot()

    def _load_schedule(self) -> List[ForecastPoint]:
        with self._cache_lock:
            if self._cached_schedule and (time.time() - self._cached_schedule[0] < self.cache_ttl):
                return self._cached_schedule[1]

        start = self._floor_minute(datetime.now(timezone.utc))
        url = f"{self.base_url}{self._build_schedule_path(start)}"

        if requests is None:
            return []

        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return []

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []

        schedule = self._normalise_schedule(data)
        if not schedule:
            return []

        # Log the fetched carbon intensity
        if schedule:
            _LOGGER.info(
                "Fetched carbon intensity: now=%s, next=%s",
                schedule[0].forecast,
                schedule[1].forecast if len(schedule) > 1 else "N/A",
            )

        with self._cache_lock:
            self._cached_schedule = (time.time(), schedule)
        return schedule

    def _normalise_schedule(self, entries: List[Any]) -> List[ForecastPoint]:
        horizon: List[ForecastPoint] = []
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=30)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            start_ts = self._parse_time(entry.get("from"))
            end_ts = self._parse_time(entry.get("to"))
            if start_ts is None or end_ts is None:
                continue
            if end_ts < window_start:
                continue
            intensity = self._extract_forecast(entry.get("intensity"))
            point = ForecastPoint(
                start=start_ts,
                end=end_ts,
                forecast=intensity,
                index=self._extract_index(entry.get("intensity")),
            )
            horizon.append(point)

        horizon.sort(key=lambda item: item.start)
        return horizon

    def _build_schedule_path(self, start: datetime) -> str:
        period_start = start.strftime("%Y-%m-%dT%H:%MZ")
        if self._target_type == "region" and self._target_value:
            return f"/regional/intensity/{period_start}/fw48h/regionid/{self._target_value}"
        if self._target_type == "postcode" and self._target_value:
            return f"/regional/intensity/{period_start}/fw48h/postcode/{self._target_value}"
        return f"/intensity/{period_start}/fw48h"

    def _fetch_legacy(self) -> Optional[ForecastSnapshot]:
        url = self._configured_base or self.base_url
        if not url:
            return None
        if not url.endswith("/forecast"):
            url = url.rstrip("/") + "/forecast"

        if requests is None:
            return None

        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return None

        intensity_now = payload.get("current") or payload.get("intensity_now")
        intensity_next = payload.get("next") or payload.get("intensity_next")

        now_value = self._to_float(intensity_now)
        next_value = self._to_float(intensity_next)

        if now_value is None and next_value is None:
            return None

        return ForecastSnapshot(intensity_now=now_value, intensity_next=next_value)

    @staticmethod
    def _parse_target(raw: str) -> tuple[str, Optional[str]]:
        value = (raw or "national").strip()
        lowered = value.lower()
        if lowered.startswith("region:"):
            return "region", value.split(":", 1)[1].strip()
        if lowered.startswith("postcode:"):
            return "postcode", value.split(":", 1)[1].strip().upper()
        return "national", None

    @staticmethod
    def _floor_minute(moment: datetime) -> datetime:
        rounded = moment.astimezone(timezone.utc)
        return rounded.replace(second=0, microsecond=0)

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        if not isinstance(value, str):
            return None
        candidate = value
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate).astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _extract_forecast(blob: Any) -> Optional[float]:
        if not isinstance(blob, dict):
            return None
        value = blob.get("forecast")
        if value is None:
            value = blob.get("actual")
        return CarbonForecastProvider._to_float(value)

    @staticmethod
    def _extract_index(blob: Any) -> Optional[str]:
        if not isinstance(blob, dict):
            return None
        label = blob.get("index")
        if isinstance(label, str):
            return label
        return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


@dataclass
class DemandEstimate:
    current: float
    next_: float


class DemandEstimator:
    """Simple exponential smoothing demand predictor."""

    def __init__(self, smoothing: float = 0.3, horizon: float = 60.0) -> None:
        self.smoothing = smoothing
        self.horizon = horizon
        self._lock = threading.Lock()
        self._rate: Optional[float] = None
        self._last_timestamp: Optional[float] = None

    def update(self, request_count: int, window_seconds: float) -> None:
        if window_seconds <= 0:
            return
        rate = request_count / window_seconds
        with self._lock:
            if self._rate is None:
                self._rate = rate
            else:
                self._rate = self.smoothing * rate + (1 - self.smoothing) * self._rate
            self._last_timestamp = time.time()

    def forecast(self) -> DemandEstimate:
        with self._lock:
            if self._rate is None:
                return DemandEstimate(0.0, 0.0)
            current = self._rate
        # Keep next horizon identical for now; can be refined when real data is available.
        return DemandEstimate(current=current, next_=current)


class ForecastManager:
    """Orchestrates multiple providers to create a combined snapshot."""

    def __init__(self, carbon_provider: CarbonForecastProvider, demand_estimator: DemandEstimator) -> None:
        self._carbon = carbon_provider
        self._demand = demand_estimator

    def snapshot(self) -> ForecastSnapshot:
        carbon = self._carbon.fetch()
        demand = self._demand.forecast()
        carbon.demand_now = demand.current
        carbon.demand_next = demand.next_
        return carbon
