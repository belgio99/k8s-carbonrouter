#!/usr/bin/env python3
"""
carbonrouter_router.py
────────────────────────────────────────────────────────────────────────────
HTTP → RabbitMQ router with “direct/queue” load balancing based on a
CustomResource (TrafficSchedule).  Espone metriche Prometheus.
"""
from __future__ import annotations

import uvloop
uvloop.install() # type: ignore

import asyncio
import json
import os
import signal
import sys
import uuid
import time
from typing import Any, Dict

import aio_pika
from aio_pika import ExchangeType, Queue
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

from common.utils import b64dec, b64enc, debug, log, weighted_choice
from common.schedule import TrafficScheduleManager

# ────────────────────────────────────
# Config
# ────────────────────────────────────
RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
TS_NAME: str = os.getenv("TS_NAME", "traffic-schedule")
TS_NAMESPACE: str = os.getenv("TS_NAMESPACE", "default")
METRICS_PORT: int = int(os.getenv("METRICS_PORT", "8001"))
TARGET_SVC_NAME: str = os.getenv("TARGET_SVC_NAME", "unknown-svc").lower()
TARGET_SVC_NAMESPACE: str = os.getenv("TARGET_SVC_NAMESPACE", "default").lower()

RPC_TIMEOUT_SEC: float = float(os.getenv("RPC_TIMEOUT_SEC", "60"))

# ────────────────────────────────────
# Prometheus metrics
# ────────────────────────────────────
INGRESS_HTTP_REQUESTS = Counter(
    "router_ingress_http_requests_total",
    "HTTP requests observed at the router ingress",
    ["method", "status", "qtype", "flavour", "forced"],
)

HTTP_LATENCY = Histogram(
    "router_request_duration_seconds",
    "End-to-end latency",
    ["qtype", "flavour"],
)

PUBLISHED_MESSAGES = Counter(
    "router_messages_published_total",
    "Messages published",
    ["queue"],
)

SCHEDULE_TTL = Gauge(
    "router_schedule_valid_seconds",
    "Seconds until schedule expiry",
)

# ────────────────────────────────────
# RabbitMQ state  (connection reused)
# ────────────────────────────────────
rabbit_state: dict[str, Any] = {
    "channel": None,
    "connection": None,
    "reply_queue": None,  # a single reply-queue
    "pending": {},
}

init_lock = asyncio.Lock()


async def _init_rabbit() -> None:
    """
    Opens connection/channel, declares headers-exchange and the single reply
    queue (idempotente: viene chiamato la prima volta che serve un canale).
    """
    async with init_lock:
        if rabbit_state["channel"] and not rabbit_state["channel"].is_closed:
            return  # già inizializzato
        
        if rabbit_state.get("exchange"):
            return

        # Connessione e channel robust
        rabbit_state["connection"] = await aio_pika.connect_robust(RABBITMQ_URL)
        rabbit_state["channel"] = await rabbit_state["connection"].channel()

        # Headers-exchange per la pubblicazione
        rabbit_state["exchange"] = await rabbit_state["channel"].declare_exchange(
            f"{TARGET_SVC_NAMESPACE}.{TARGET_SVC_NAME}",
            ExchangeType.HEADERS,
            durable=True,
        )


        # Consumer fisso che demultiplexa via correlation_id
        async def _on_reply(msg: aio_pika.IncomingMessage) -> None:
            future: asyncio.Future | None = rabbit_state["pending"].pop(
                msg.correlation_id, None
            )
            if future and not future.done():
                future.set_result(msg)

        reply_queue = Queue(
            rabbit_state["channel"],
            name="amq.rabbitmq.reply-to",
            passive=True,
            durable=False,
            exclusive=False,
            auto_delete=False,
            arguments=None,
        )
        await reply_queue.consume(_on_reply, no_ack=True)


async def get_rabbit_channel() -> aio_pika.Channel:
    """Restituisce il canale AMQP già inizializzato."""
    await _init_rabbit()
    return rabbit_state["channel"]

async def get_exchange() -> aio_pika.Exchange:   # NEW
    await _init_rabbit()
    return rabbit_state["exchange"]


# ────────────────────────────────────
# FastAPI router
# ────────────────────────────────────
def create_app(schedule_manager: TrafficScheduleManager) -> FastAPI:
    """
    Builds the FastAPI instance with:
      • /metrics endpoint
      • catch-all proxy that forwards to RabbitMQ
    """
    app = FastAPI(title="carbonrouter-router", docs_url=None, redoc_url=None)

    # ───────────── catch-all proxy ─────────────
    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(full_path: str, request: Request) -> Response:  # noqa: C901
        debug(f"Proxy start: method={request.method} path=/{full_path}")
        start_ts = time.perf_counter()
        schedule = await schedule_manager.snapshot()

        # ─── select strategy / flavour ───
        urgent = request.headers.get("x-urgent", "false").lower() == "true"
        forced_flavour = request.headers.get("x-carbonrouter")

        # Read flavours from TrafficSchedule status (not flavourRules)
        # Structure: [{"precision": 30, "weight": 8}, {"precision": 50, "weight": 8}, ...]
        flavours = schedule.get("flavours", [])
        if not flavours:
            # No schedule available - FAIL the request
            return Response(
                content=b'{"error": "No TrafficSchedule available - router cannot route requests"}',
                status_code=503,
                media_type="application/json",
            )
        
        # Build flavour_weights from flavours array
        flavour_weights = {}
        for f in flavours:
            precision = f.get("precision")
            weight = f.get("weight", 0)
            if precision is not None:
                flavour_name = f"precision-{int(precision)}"
                flavour_weights[flavour_name] = int(weight)

        headers: Dict[str, str] = dict(request.headers)
        if urgent:
            headers["x-carbonrouter-urgent"] = "true"

        candidate_weights = {k: v for k, v in flavour_weights.items() if v > 0}
        if not candidate_weights:
            # All weights are zero - cannot route
            return Response(
                content=b'{"error": "All flavours have zero weight - cannot route"}',
                status_code=503,
                media_type="application/json",
            )

        if forced_flavour and forced_flavour in candidate_weights:
            flavour = forced_flavour
        else:
            flavour = forced_flavour or weighted_choice(candidate_weights)
        q_type = "queue"
        debug(
            f"Selected routing: q_type={q_type}, flavour={flavour}, forced={bool(forced_flavour)}, urgent={urgent}"
        )
        # ─── build payload ───
        payload = {
            "method": request.method,
            "path": f"/{full_path}",
            "query": str(request.query_params),
            "headers": headers,
            "body": b64enc(await request.body()),
            "forced": bool(forced_flavour),
        }

        # ─── publish ───
        correlation_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        response_future: "asyncio.Future[aio_pika.IncomingMessage]" = (
            loop.create_future()
        )
        rabbit_state["pending"][correlation_id] = response_future

        exchange = await get_exchange()

        await exchange.publish(
            aio_pika.Message(
                json.dumps(payload).encode(),
                correlation_id=correlation_id,
                reply_to="amq.rabbitmq.reply-to",
                headers={
                    "q_type": q_type,
                    "flavour": flavour,
                    "namespace": TARGET_SVC_NAMESPACE,
                    "service": TARGET_SVC_NAME,
                },
            ),
            routing_key="",  # ignorato dal headers-exchange
            mandatory=True,
        )
        PUBLISHED_MESSAGES.labels(
            queue=f"{TARGET_SVC_NAMESPACE}.{TARGET_SVC_NAME}.{q_type}.{flavour}"
        ).inc()
        debug(
            "Published message: "
            f"headers={{q_type:{q_type}, flavour:{flavour}}}, "
            f"correlation_id={correlation_id}"
        )

        # ─── wait for RPC response ───
        try:
            rabbit_msg = await asyncio.wait_for(response_future, timeout=max(RPC_TIMEOUT_SEC, 1.0))
        except asyncio.TimeoutError as exc:
            rabbit_state["pending"].pop(correlation_id, None)
            HTTP_LATENCY.labels(q_type, flavour).observe(time.perf_counter() - start_ts)
            INGRESS_HTTP_REQUESTS.labels(
                request.method, "504", q_type, flavour, bool(forced_flavour)
            ).inc()
            raise HTTPException(status_code=504, detail="Upstream timeout") from exc

        response_data = json.loads(rabbit_msg.body)

        status_code = int(response_data.get("status", 200))
        INGRESS_HTTP_REQUESTS.labels(
            request.method, str(status_code), q_type, flavour, bool(forced_flavour)
        ).inc()
        HTTP_LATENCY.labels(q_type, flavour).observe(time.perf_counter() - start_ts)

        response_headers = {
            k: v
            for k, v in response_data.get("headers", {}).items()
            if k.lower() != "content-length"
        }

        return Response(
            b64dec(response_data["body"]),
            status_code=status_code,
            headers=response_headers,
            media_type=response_data.get("headers", {}).get(
                "content-type", "application/octet-stream"
            ),
        )

    return app



# ────────────────────────────────────
# Main
# ────────────────────────────────────
async def main() -> None:
    # Prometheus
    start_http_server(METRICS_PORT)

    schedule_mgr = TrafficScheduleManager(TS_NAME, TS_NAMESPACE)

    loop = asyncio.get_running_loop()
    
    # Load schedule FIRST - don't start server until we have a valid schedule
    log.info("Loading initial TrafficSchedule...")
    await schedule_mgr.load_once()
    
    # Verify we have flavours before starting
    initial_schedule = await schedule_mgr.snapshot()
    flavours = initial_schedule.get("flavours", [])
    if not flavours:
        log.error("FATAL: TrafficSchedule has no flavours - cannot start router")
        log.error("Router requires a valid TrafficSchedule with flavours to operate")
        sys.exit(1)
    
    flavour_list = [f"precision-{f.get('precision')}" for f in flavours if f.get("precision")]
    log.info(f"Router ready with flavours: {flavour_list}")
    
    # Now start background tasks
    loop.create_task(schedule_mgr.watch_forever())
    loop.create_task(schedule_mgr.expiry_guard())

    app = create_app(schedule_mgr)
    log_level = "info" if os.getenv("DEBUG", "false").lower() == "true" else "warning"
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8000, lifespan="off", log_level=log_level)
    )
    loop.create_task(server.serve())

    # graceful-shutdown
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    await stop_event.wait()
    await schedule_mgr.close()
    # chiude anche il rabbit se presente
    if rabbit_state.get("connection"):
        await rabbit_state["connection"].close()


if __name__ == "__main__":
    asyncio.run(main())
