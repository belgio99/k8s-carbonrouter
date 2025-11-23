#!/usr/bin/env python3
"""
consumer.py
────────────────────────────────────────────────────────────────────────────
Consumes the AMQP queues populated by carbonrouter-router, forwards the embedded
HTTP request to the target service and answers via AMQP (RPC style).
"""

# ──────────────────────────────────────────────────────────────
# Faster event-loop (must be done BEFORE importing asyncio)
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import uvloop  # type: ignore
uvloop.install()


import asyncio
import json
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Coroutine

import aio_pika
from aio_pika import ExchangeType
from aio_pika.pool import Pool
import httpx
import uvicorn
from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

from common.schedule import TrafficScheduleManager
from common.utils import b64dec, b64enc, debug, log, weighted_choice

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

TARGET_SVC_NAMESPACE: str = os.getenv("TARGET_SVC_NAMESPACE", "default").lower()
TARGET_SVC_NAME: str = os.getenv("TARGET_SVC_NAME", "unknown-svc").lower()
TARGET_SVC_SCHEME: str = os.getenv("TARGET_SVC_SCHEME", "http")
TARGET_SVC_PORT: str | None = os.getenv("TARGET_SVC_PORT")

TARGET_BASE_URL: str = (
    f"{TARGET_SVC_SCHEME}://{TARGET_SVC_NAME}.{TARGET_SVC_NAMESPACE}.svc.cluster.local"
    + (f":{TARGET_SVC_PORT}" if TARGET_SVC_PORT else "")
)

TS_NAME: str = os.getenv("TS_NAME", "traffic-schedule")
TS_NAMESPACE: str = os.getenv("TS_NAMESPACE", "default")
METRICS_PORT: int = int(os.getenv("METRICS_PORT", "8001"))
QUEUE_PREFIX: str = f"{TARGET_SVC_NAMESPACE}.{TARGET_SVC_NAME}"
EXCHANGE_NAME: str = QUEUE_PREFIX

# Per-queue concurrency (can be tuned by ENV)
CONCURRENCY: int = int(os.getenv("CONCURRENCY_PER_QUEUE", "32"))

CONSUMER_THROTTLE_ENABLED: bool = (
    os.getenv("CONSUMER_THROTTLE_ENABLED", "true").lower() == "true"
)
CONSUMER_THROTTLE_REFRESH_SECONDS: float = float(
    os.getenv("CONSUMER_THROTTLE_REFRESH_SECONDS", "1.5")
)
CONSUMER_THROTTLE_EXPONENT: float = float(
    os.getenv("CONSUMER_THROTTLE_EXPONENT", "3.0")
)
CONSUMER_THROTTLE_MIN_INFLIGHT: int = int(
    os.getenv("CONSUMER_THROTTLE_MIN_INFLIGHT", "1")
)

# ──────────────────────────────────────────────────────────────
# Prometheus metrics
# ──────────────────────────────────────────────────────────────
MSG_CONSUMED = Counter(
    "consumer_messages_total",
    "AMQP messages consumed",
    ["queue_type", "flavour"],
)
HTTP_FORWARD_LAT = Histogram(
    "consumer_forward_seconds",
    "Time spent forwarding the HTTP request",
    ["flavour"],
)
PROCESSED_HTTP_REQUESTS = Counter(
    "router_http_requests_total",
    "HTTP requests processed after buffering",
    ["method", "status", "qtype", "flavour", "forced"],
)

PROCESSING_THROTTLE_FACTOR = Gauge(
    "consumer_processing_throttle_factor",
    "Throttle factor read from the TrafficSchedule status",
    ["scope"],
)
PROCESSING_THROTTLE_LIMIT = Gauge(
    "consumer_processing_inflight_limit",
    "Current in-flight cap enforced by the consumer-side throttle",
    ["scope"],
)
PROCESSING_THROTTLE_INFLIGHT = Gauge(
    "consumer_processing_inflight_active",
    "Active in-flight forwards tracked by the consumer-side throttle",
    ["scope"],
)


class ProcessingThrottle:
    """Controls the amount of in-flight work based on scheduler throttle."""

    def __init__(
        self,
        schedule: TrafficScheduleManager,
        per_queue_concurrency: int,
    ) -> None:
        self._schedule = schedule
        self._per_queue_concurrency = max(1, per_queue_concurrency)
        self._refresh_seconds = max(0.5, CONSUMER_THROTTLE_REFRESH_SECONDS)
        self._exponent = max(1.0, CONSUMER_THROTTLE_EXPONENT)
        self._min_inflight = max(1, CONSUMER_THROTTLE_MIN_INFLIGHT)
        self._condition = asyncio.Condition()
        self._limit = self._per_queue_concurrency
        self._inflight = 0
        self._factor = 1.0
        self._task: asyncio.Task | None = None
        PROCESSING_THROTTLE_FACTOR.labels("global").set(self._factor)
        PROCESSING_THROTTLE_LIMIT.labels("global").set(self._limit)
        PROCESSING_THROTTLE_INFLIGHT.labels("global").set(0)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None, None]:
        await self._acquire()
        try:
            yield
        finally:
            await self._release()

    async def _acquire(self) -> None:
        async with self._condition:
            while self._inflight >= self._limit:
                await self._condition.wait()
            self._inflight += 1
            PROCESSING_THROTTLE_INFLIGHT.labels("global").set(self._inflight)

    async def _release(self) -> None:
        async with self._condition:
            self._inflight = max(0, self._inflight - 1)
            PROCESSING_THROTTLE_INFLIGHT.labels("global").set(self._inflight)
            self._condition.notify(1)

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self._recompute_limit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("Throttle refresh failed: %s", exc)
            await asyncio.sleep(self._refresh_seconds)

    async def _recompute_limit(self) -> None:
        schedule = await self._schedule.snapshot()
        
        # 1. Try top-level "processingThrottle" (operator CR status format)
        raw_factor = schedule.get("processingThrottle")
        
        # 2. Fallback to nested "processing" -> "throttle" (decision engine API format)
        if raw_factor is None:
            processing = schedule.get("processing", {})
            if isinstance(processing, dict):
                raw_factor = processing.get("throttle")

        # 3. Default to 1.0 if missing
        if raw_factor is None:
            raw_factor = 1.0

        try:
            factor = float(raw_factor)
        except (TypeError, ValueError):
            factor = 1.0
        factor = max(0.0, min(1.0, factor))
        flavours = schedule.get("flavours") or []
        flavour_count = max(1, len(flavours))
        max_concurrency = self._per_queue_concurrency * flavour_count
        limit_float = max_concurrency * (factor ** self._exponent)
        if factor >= 0.999:
            new_limit = max_concurrency
        else:
            new_limit = max(self._min_inflight, int(round(limit_float)))
        async with self._condition:
            changed = new_limit != self._limit or abs(factor - self._factor) > 1e-3
            self._factor = factor
            self._limit = max(self._min_inflight, new_limit)
            PROCESSING_THROTTLE_FACTOR.labels("global").set(self._factor)
            PROCESSING_THROTTLE_LIMIT.labels("global").set(self._limit)
            if changed:
                self._condition.notify_all()
        if changed:
            log.info(
                "Consumer throttle updated: factor=%.3f limit=%d (max=%d)",
                self._factor,
                self._limit,
                max_concurrency,
            )


async def select_target_flavour(
    schedule_mgr: TrafficScheduleManager,
    queue_flavour: str,
    forced: bool,
) -> str:
    """Return the target flavour after applying evaluator rules."""

    if forced:
        return queue_flavour

    schedule = await schedule_mgr.snapshot()
    evaluator = str(schedule.get("routingEvaluator", "router")).lower()
    if evaluator != "consumer":
        return queue_flavour

    flavours = schedule.get("flavours") or []
    weights: dict[str, int] = {}
    for flavour_info in flavours:
        precision = flavour_info.get("precision")
        weight = flavour_info.get("weight", 0)
        if precision is None:
            continue
        try:
            precision_int = int(precision)
        except (TypeError, ValueError):
            continue
        try:
            weight_int = int(weight)
        except (TypeError, ValueError):
            continue
        flavour_name = f"precision-{precision_int}"
        weights[flavour_name] = weight_int

    positive = {name: val for name, val in weights.items() if val > 0}
    if not positive:
        return queue_flavour

    selected = weighted_choice(positive)
    if not selected:
        return queue_flavour
    return selected
MAX_RETRIES          = 5
BACKOFF_FIRST_DELAY  = 1.0
BACKOFF_FACTOR       = 2
RETRYABLE_STATUS     = {500, 502, 503, 504}
RETRYABLE_EXC        = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


class FlavourWorkerManager:
    """Maintains AMQP consumers for each discovered flavour."""

    def __init__(
        self,
        schedule: TrafficScheduleManager,
        listen_channel: aio_pika.Channel,
        exchange: aio_pika.Exchange,
        channel_pool: Pool,
        http_client: httpx.AsyncClient,
        processing_throttle: ProcessingThrottle | None = None,
        poll_interval: int = 10,
    ) -> None:
        self._schedule = schedule
        self._listen_channel = listen_channel
        self._exchange = exchange
        self._channel_pool = channel_pool
        self._http_client = http_client
        self._poll_interval = poll_interval
        self._processing_throttle = processing_throttle
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._lock = asyncio.Lock()

    async def sync_from_schedule(self) -> None:
        flavours = set(await self._schedule.flavour_names())
        await self._sync(flavours)

    async def reconcile_loop(self) -> None:
        while True:
            try:
                await self.sync_from_schedule()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to reconcile flavours: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _sync(self, desired: set[str]) -> None:
        async with self._lock:
            current = set(self._tasks.keys())

            for flavour in desired - current:
                tasks = [
                    self._create_task(
                        flavour,
                        consume_buffer_queue(
                            self._listen_channel,
                            self._exchange,
                            flavour,
                            self._schedule,
                            self._channel_pool,
                            self._http_client,
                            self._processing_throttle,
                        ),
                    )
                ]
                self._tasks[flavour] = tasks
                log.info("Started consumers for flavour %s", flavour)

            for flavour in current - desired:
                for task in self._tasks.pop(flavour, []):
                    task.cancel()
                log.info("Stopped consumers for flavour %s", flavour)

    def _create_task(
        self, flavour: str, coro: Coroutine[Any, Any, None]
    ) -> asyncio.Task:
        task = asyncio.create_task(coro)
        task.add_done_callback(lambda t, name=flavour: self._on_task_done(name, t))
        return task

    def _on_task_done(self, flavour: str, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.error("Worker for flavour %s crashed: %s", flavour, exc)

# ──────────────────────────────────────────────────────────────
# FastAPI – only /metrics
# ──────────────────────────────────────────────────────────────
app = FastAPI(title="carbonrouter-consumer", docs_url=None, redoc_url=None)


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

async def send_with_retry(http_client: httpx.AsyncClient, **req_kw):
    """Send an HTTP request to target services with retry logic."""
    delay = BACKOFF_FIRST_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = await http_client.request(**req_kw)
            if r.status_code not in RETRYABLE_STATUS:
                return r
            raise RuntimeError(f"status {r.status_code}")
        except (*RETRYABLE_EXC, RuntimeError) as exc:
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(delay)
            delay *= BACKOFF_FACTOR


# ──────────────────────────────────────────────────────────────
# HTTP forward + AMQP reply
# ──────────────────────────────────────────────────────────────
async def forward_and_reply(
    message: aio_pika.IncomingMessage,
    flavour: str,
    channel_pool: Pool,
    http_client: httpx.AsyncClient,
    schedule_mgr: TrafficScheduleManager,
) -> tuple[int, float, str, bool, bool, str]:
    """
    Execute the HTTP request embedded in `message` and publish the response
    to `message.reply_to`.

    Returns a tuple (status_code, elapsed_seconds, method, forced, delivered)
    so callers can update metrics only when the request is fully processed.
    """
    start_ts = time.perf_counter()
    method = "UNKNOWN"
    forced = False

    try:
        payload: Dict[str, Any] = json.loads(message.body)
        method = str(payload.get("method", method))
        forced = bool(payload.get("forced", forced))
        debug(
            f"Payload: method={payload.get('method')} path={payload.get('path')} headers={payload.get('headers')}"
        )
        # Extract precision number from flavour name (e.g., "precision-100" -> "100")
        flavour = await select_target_flavour(schedule_mgr, flavour, forced)
        precision_value = flavour.split("-")[-1] if "-" in flavour else flavour
        response = await send_with_retry(
            http_client,
            method=payload["method"],
            url=f"{TARGET_BASE_URL}{payload['path']}",
            params=payload.get("query"),
            headers={**payload.get("headers", {}), "x-carbonrouter": precision_value},
            content=b64dec(payload["body"]),
        )
        status_code = response.status_code
        response_headers = dict(response.headers)
        response_body = response.content

    except Exception as exc:  # network / decode failure
        status_code = 500
        response_headers = {"content-type": "application/json"}
        response_body = json.dumps({"error": str(exc)}).encode()

        await message.nack(requeue=True)

        debug(f"Error processing message: {exc}")
        return 500, time.perf_counter() - start_ts, method, forced, False, flavour

    # Publish RPC reply using a pooled channel (avoids single-channel lock)
    async with channel_pool.acquire() as publish_ch:
        await publish_ch.default_exchange.publish(
            aio_pika.Message(
                json.dumps(
                    {
                        "status": status_code,
                        "headers": response_headers,
                        "body": b64enc(response_body),
                    }
                ).encode(),
                correlation_id=message.correlation_id,
            ),
            routing_key=message.reply_to,
        )
    await message.ack()

    elapsed = time.perf_counter() - start_ts
    return status_code, elapsed, method, forced, True, flavour

# ──────────────────────────────────────────────────────────────
# Worker – buffer path (queue.*)  pausable via TrafficSchedule
# ──────────────────────────────────────────────────────────────
async def consume_buffer_queue(
    listen_channel: aio_pika.Channel,
    exchange: aio_pika.Exchange,
    flavour: str,
    schedule_mgr: TrafficScheduleManager,
    channel_pool: Pool,
    http_client: httpx.AsyncClient,
    processing_throttle: ProcessingThrottle | None = None,
) -> None:
    """
    Consume <prefix>.queue.<flavour> continuously.
    """
    queue_name = f"{QUEUE_PREFIX}.queue.{flavour}"
    queue = await listen_channel.declare_queue(queue_name, durable=True)
    await queue.bind(
        exchange,
        arguments={"x-match": "all", "q_type": "queue", "flavour": flavour},
    )
    debug(f"Queue declared once: {queue_name}")

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _handle_message(message: aio_pika.IncomingMessage) -> None:
            queue_flavour = message.headers.get("flavour", flavour)
            q_type = message.headers.get("q_type", "queue")
            (
                status,
                dt_sec,
                method,
                forced,
                delivered,
                effective_flavour,
            ) = await forward_and_reply(
                message,
                queue_flavour,
                channel_pool,
                http_client,
                schedule_mgr,
            )
            MSG_CONSUMED.labels("queue", queue_flavour).inc()
            if delivered:
                PROCESSED_HTTP_REQUESTS.labels(
                    method,
                    str(status),
                    q_type,
                    effective_flavour,
                    str(forced),
                ).inc()
            HTTP_FORWARD_LAT.labels(effective_flavour).observe(dt_sec)

    async def _on_message(message: aio_pika.IncomingMessage) -> None:
        async with sem:
            if processing_throttle is not None:
                async with processing_throttle.slot():
                    await _handle_message(message)
            else:
                await _handle_message(message)

    await queue.consume(_on_message, no_ack=False)
    await asyncio.Event().wait()


# ──────────────────────────────────────────────────────────────
# Main bootstrap
# ──────────────────────────────────────────────────────────────
async def main() -> None:
    print("Starting carbonrouter consumer...")
    if os.getenv("DEBUG", "false").lower() != "true":
        logging.getLogger("httpx").setLevel(logging.WARNING)
    # Prometheus endpoint
    start_http_server(METRICS_PORT)
    print(f"Prometheus metrics available at /metrics, port {METRICS_PORT}")
    # TrafficSchedule manager (background task)
    schedule_mgr = TrafficScheduleManager(TS_NAME, TS_NAMESPACE)
    await schedule_mgr.load_once()
    asyncio.create_task(schedule_mgr.watch_forever())
    asyncio.create_task(schedule_mgr.expiry_guard())

    # ── AMQP pools ────────────────────────────────────────────
    connection_pool: Pool[aio_pika.RobustConnection] = Pool(
        lambda: aio_pika.connect_robust(RABBITMQ_URL), max_size=2
    )

    async def _get_channel() -> aio_pika.RobustChannel:
        """Return a new channel from the pooled connection."""
        async with connection_pool.acquire() as conn:
            return await conn.channel()

    channel_pool: Pool[aio_pika.RobustChannel] = Pool(_get_channel, max_size=64)

    # Dedicated connection/channel for consuming
    listen_connection = await aio_pika.connect_robust(RABBITMQ_URL)
    listen_channel = await listen_connection.channel()
    await listen_channel.set_qos(prefetch_count=CONCURRENCY * 2)

    # Headers-exchange
    exchange = await listen_channel.declare_exchange(
        EXCHANGE_NAME, ExchangeType.HEADERS, durable=True
    )

    # Shared HTTP client
    http_client = httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(max_connections=128, max_keepalive_connections=32),
        timeout=httpx.Timeout(10.0),
    )

    processing_throttle: ProcessingThrottle | None = None
    if CONSUMER_THROTTLE_ENABLED:
        processing_throttle = ProcessingThrottle(schedule_mgr, CONCURRENCY)
        processing_throttle.start()
        log.info(
            "Consumer-side throttling enabled (refresh=%.1fs exponent=%.2f)",
            CONSUMER_THROTTLE_REFRESH_SECONDS,
            CONSUMER_THROTTLE_EXPONENT,
        )
    else:
        log.info("Consumer-side throttling disabled via CONSUMER_THROTTLE_ENABLED")

    # Spawn workers per flavour
    flavour_manager = FlavourWorkerManager(
        schedule_mgr,
        listen_channel,
        exchange,
        channel_pool,
        http_client,
        processing_throttle=processing_throttle,
    )
    await flavour_manager.sync_from_schedule()
    asyncio.create_task(flavour_manager.reconcile_loop())

    # FastAPI (only /metrics) – no lifespan
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, lifespan="off", log_level="info")
    asyncio.create_task(uvicorn.Server(config).serve())

    # ── Graceful shutdown ─────────────────────────────────────
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    await stop_event.wait()

    await listen_connection.close()
    await http_client.aclose()
    if processing_throttle is not None:
        await processing_throttle.stop()
    await schedule_mgr.close()


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())