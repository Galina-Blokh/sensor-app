"""Async FastAPI entrypoint.

**Polars** / SQLite CPU and blocking I/O run in worker threads via
:class:`asyncio.to_thread` so request handlers stay non-blocking.

**Cross-cutting:** :class:`~sensor_app.api.middleware.RequestIdMiddleware` (``X-Request-ID``)
and **slowapi** rate limits per route (see :class:`sensor_app.settings.Settings`).

Routes: ``GET /`` (index), ``GET /health``, ``POST /process/{station_id}``,
``GET /metrics/{station_id}``, optional ``GET /stream/status`` and ``POST /stream/flush``
when a queue-backed consumer is wired (``spec_ch3``), LLM routes under ``POST /llm/*``
(OpenAI-compatible providers e.g. OpenAI, Groq, self-hosted; optional primary+fallback chain)
plus FastAPI ``/docs``.
"""

from __future__ import annotations

import asyncio
import logging
import queue
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, cast

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from sensor_app.api.middleware import RequestIdMiddleware, configure_app_logging
from sensor_app.lib.metrics_store import MetricsStore, StoredSnapshot
from sensor_app.lib.pipeline import MissingDataStrategy, PipelineConfig, run_station_pipeline
from sensor_app.lib.schema_defs import LoadedSchema
from sensor_app.lib.stream_consumer import StreamProcessor
from sensor_app.llm.client import (
    LLMBackend,
    OpenAICompatibleChatBackend,
    PrimaryWithFallbackBackend,
)
from sensor_app.llm.exceptions import LLMError
from sensor_app.llm.service import LLMFeatureService
from sensor_app.settings import Settings

logger = logging.getLogger(__name__)

# Sentinel: ``queue.Queue.get`` timed out (no item yet).
_QUEUE_EMPTY = object()

# Shared limiter instance; per-route strings come from :class:`Settings` at mount time.
limiter = Limiter(key_func=get_remote_address)


def rate_limit_exception_handler(request: Request, exc: Exception) -> Response:
    """Starlette types handler ``exc`` as :class:`Exception`; delegate to slowapi after narrow."""
    assert isinstance(exc, RateLimitExceeded)
    return _rate_limit_exceeded_handler(request, exc)


class ProcessResponse(BaseModel):
    """Body returned by ``POST /process/{station_id}`` after a snapshot is stored."""

    station_id: str
    snapshot_id: int
    window_start: str | None
    window_end: str | None
    data_quality_score: float


class LLMStationWindowBody(BaseModel):
    """Resolve stored metrics/DQ for **station_id**; optional window filters list order."""

    station_id: str
    snapshot_id: int | None = Field(
        default=None,
        description="Explicit row id; when omitted, uses newest snapshot in optional window",
    )
    start_time: str | None = Field(default=None, description="ISO8601 filter (see GET /metrics)")
    end_time: str | None = Field(default=None, description="ISO8601 filter (see GET /metrics)")


class LLMQueryBody(BaseModel):
    """Plain-English question answered via structured metric access only."""

    station_id: str
    question: str = Field(..., min_length=1)
    start_time: str | None = None
    end_time: str | None = None


class LLMTextSummaryResponse(BaseModel):
    """NL summary; ``model`` is the host model id that produced the text (primary or fallback)."""

    summary: str
    snapshot_id: int
    model: str


class LLMQueryResponse(BaseModel):
    """Answer from Python over stored metrics; ``model`` is the host that produced the JSON plan."""

    answer: str
    facts: dict[str, Any]
    model: str


def get_settings(request: Request) -> Settings:
    """Resolve :class:`sensor_app.settings.Settings` bound at app creation (test-friendly).

    Starlette's ``state`` is a dynamic namespace; we narrow the type assigned in
    :func:`create_app`.
    """
    return cast(Settings, request.app.state.settings)


def parse_query_datetime(value: str | None) -> datetime | None:
    """Parse query ``Z`` suffix and raise 400 on malformed ISO strings."""
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {value}") from e


def pipeline_config_from_settings(settings: Settings) -> PipelineConfig:
    """Map settings to :class:`PipelineConfig`; invalid enum falls back to interpolate."""
    try:
        strat = MissingDataStrategy(settings.missing_data_strategy)
    except ValueError:
        strat = MissingDataStrategy.INTERPOLATE
    return PipelineConfig(
        resample_rule=settings.default_resample_rule,
        missing_strategy=strat,
        motor_on_threshold_rpm=settings.power_on_motor_threshold_rpm,
        power_on_threshold_kw=settings.power_on_power_threshold_kw,
        flow_epsilon_m3h=settings.flow_epsilon_m3h,
    )


def _filter_snapshots_metric_key(
    rows: list[dict[str, Any]],
    metric_key: str,
) -> list[dict[str, Any]]:
    """Keep only **metric_key** in each device's ``metrics`` map (``spec_ch3`` filter)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        m = dict(row.get("metrics") or {})
        devs = m.get("devices")
        if not isinstance(devs, list):
            out.append(row)
            continue
        new_devs: list[dict[str, Any]] = []
        for d in devs:
            if not isinstance(d, dict):
                new_devs.append(d)
                continue
            vals = d.get("metrics")
            if not isinstance(vals, dict):
                new_devs.append(d)
                continue
            v = vals.get(metric_key)
            new_devs.append(
                {
                    **d,
                    "metrics": {metric_key: v} if v is not None else {},
                }
            )
        out.append({**row, "metrics": {**m, "devices": new_devs}})
    return out


def _wire_llm_backend(app: FastAPI, settings: Settings) -> bool:
    """Attach OpenAI-compatible client(s) to ``app.state``. Returns whether fallback is wired."""
    app.state.llm_http_client = None
    app.state.llm_backend = None
    override = cast(LLMBackend | None, getattr(app.state, "llm_backend_override", None))
    if override is not None:
        app.state.llm_backend = override
        return False
    if not settings.llm_enabled or not settings.llm_api_key.strip():
        return False
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    client = httpx.AsyncClient(timeout=timeout)
    app.state.llm_http_client = client
    primary = OpenAICompatibleChatBackend(
        client=client,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        max_retries=settings.llm_max_retries,
        backoff_base_ms=settings.llm_backoff_base_ms,
    )
    fb_url = settings.llm_fallback_base_url.strip()
    fb_model = settings.llm_fallback_model.strip()
    use_fb = settings.llm_fallback_enabled and bool(fb_url) and bool(fb_model)
    if use_fb:
        fb_key = settings.llm_fallback_api_key.strip() or settings.llm_api_key
        secondary = OpenAICompatibleChatBackend(
            client=client,
            base_url=fb_url,
            api_key=fb_key,
            model=fb_model,
            max_retries=settings.llm_max_retries,
            backoff_base_ms=settings.llm_backoff_base_ms,
        )
        app.state.llm_backend = PrimaryWithFallbackBackend(primary, secondary)
        return True
    app.state.llm_backend = primary
    return False


def _start_stream_consumer_task(
    app: FastAPI,
    settings: Settings,
    store: MetricsStore,
    eq: queue.Queue[Any],
) -> None:
    stream_schema = LoadedSchema.from_path(settings.schema_path)
    app.state.stream_processor = StreamProcessor(
        store=store,
        schema=stream_schema,
        pipeline_config=pipeline_config_from_settings(settings),
        flush_min_events=settings.stream_flush_min_events,
        max_seen_event_ids=settings.stream_max_seen_event_ids,
    )
    stop = asyncio.Event()

    async def _queue_loop() -> None:
        processor = app.state.stream_processor
        assert isinstance(processor, StreamProcessor)
        processor.set_running(True)
        try:
            while not stop.is_set():

                def _pull() -> Any:
                    try:
                        return eq.get(timeout=0.5)
                    except queue.Empty:
                        return _QUEUE_EMPTY

                item = await asyncio.to_thread(_pull)
                if item is _QUEUE_EMPTY:
                    continue
                await asyncio.to_thread(processor.handle, item)
        finally:
            processor.set_running(False)

    app.state._stream_stop = stop
    app.state._stream_task = asyncio.create_task(_queue_loop())


async def _shutdown_stream_consumer(app: FastAPI) -> None:
    st = getattr(app.state, "_stream_stop", None)
    task = getattr(app.state, "_stream_task", None)
    if st is not None:
        st.set()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure ``metric_snapshots`` exists; optionally wire LLM client and stream consumer."""
    settings: Settings = app.state.settings
    store = MetricsStore(settings.metrics_db_path)
    store.init()
    app.state.store = store
    app.state.stream_processor = None
    app.state._stream_task = None
    app.state._stream_stop = None

    llm_fallback_configured = _wire_llm_backend(app, settings)

    eq = cast(queue.Queue[Any] | None, getattr(app.state, "event_queue", None))
    if settings.stream_consumer_enabled and eq is not None:
        _start_stream_consumer_task(app, settings, store, eq)
    elif settings.stream_consumer_enabled and eq is None:
        logger.warning(
            "stream_consumer_enabled but app.state.event_queue is missing; "
            "pass event_queue= to create_app() or use run_streaming_stack.py"
        )

    logger.info(
        "sensor_app started",
        extra={
            "sensor_db": settings.sensor_db_path,
            "metrics_db": settings.metrics_db_path,
            "llm": "on" if app.state.llm_backend is not None else "off",
            "llm_fallback": llm_fallback_configured,
            "stream_consumer": app.state.stream_processor is not None,
        },
    )
    try:
        yield
    finally:
        await _shutdown_stream_consumer(app)
        hc = getattr(app.state, "llm_http_client", None)
        if hc is not None:
            await hc.aclose()


async def root_endpoint(request: Request) -> dict[str, Any]:
    """Human/browser-friendly entry: lists main routes (``GET /`` had no handler before).

    ``request`` is required by **slowapi** for rate-limit accounting.
    """
    _ = request
    return {
        "service": "sensor_app",
        "docs": "/docs",
        "health": "/health",
        "process": "POST /process/{station_id}",
        "metrics": "GET /metrics/{station_id}",
        "llm_metrics_summary": "POST /llm/metrics-summary",
        "llm_query": "POST /llm/query",
        "llm_data_quality_summary": "POST /llm/data-quality-summary",
        "stream_status": "GET /stream/status",
        "stream_flush": "POST /stream/flush",
    }


async def health_endpoint(request: Request) -> dict[str, str]:
    """Liveness probe; no DB access. ``request`` satisfies the rate limiter."""
    _ = request
    return {"status": "ok"}


async def process_station_endpoint(
    request: Request,
    station_id: str,
    start_time: Annotated[str | None, Query(description="ISO8601 window start")] = None,
    end_time: Annotated[str | None, Query(description="ISO8601 window end")] = None,
    settings_dep: Settings = Depends(get_settings),
) -> ProcessResponse:
    """Run the full pipeline for **station_id** and persist a metric snapshot."""
    start = parse_query_datetime(start_time)
    end = parse_query_datetime(end_time)
    cfg = pipeline_config_from_settings(settings_dep)

    def _run() -> dict[str, Any]:
        # Bound for asyncio.to_thread: must be a plain callable with no request context.
        return run_station_pipeline(
            settings_dep.sensor_db_path,
            settings_dep.schema_path,
            station_id,
            start,
            end,
            cfg,
        )

    try:
        payload = await asyncio.to_thread(_run)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "process_station failed",
            extra={"request_id": getattr(request.state, "request_id", "-")},
        )
        raise HTTPException(status_code=500, detail="processing failed") from e

    computed_at = datetime.now(timezone.utc).isoformat()
    store: MetricsStore = request.app.state.store
    snap_id = await asyncio.to_thread(store.save_snapshot, payload, computed_at)

    return ProcessResponse(
        station_id=station_id,
        snapshot_id=snap_id,
        window_start=payload.get("window_start"),
        window_end=payload.get("window_end"),
        data_quality_score=float(payload.get("data_quality_score", 0.0)),
    )


async def get_metrics_endpoint(
    request: Request,
    station_id: str,
    start_time: Annotated[str | None, Query()] = None,
    end_time: Annotated[str | None, Query()] = None,
    device_id: Annotated[str | None, Query()] = None,
    metric_key: Annotated[
        str | None,
        Query(description="If set, keep only this key in each device's metrics map"),
    ] = None,
) -> list[dict[str, Any]]:
    """List saved snapshots for **station_id**, newest first; optional window/device/metric filter."""
    store: MetricsStore = request.app.state.store
    snaps = await asyncio.to_thread(
        store.list_snapshots,
        station_id,
        start_time,
        end_time,
        device_id,
    )
    rows = [
        {
            "id": s.id,
            "station_id": s.station_id,
            "window_start": s.window_start,
            "window_end": s.window_end,
            "computed_at": s.computed_at,
            "data_quality": s.data_quality,
            "metrics": s.metrics,
        }
        for s in snaps
    ]
    if metric_key and metric_key.strip():
        rows = _filter_snapshots_metric_key(rows, metric_key.strip())
    return rows


async def stream_status_endpoint(request: Request) -> dict[str, Any]:
    """Processing status + backlog depth when a stream consumer is active (``spec_ch3``)."""
    proc = getattr(request.app.state, "stream_processor", None)
    eq = getattr(request.app.state, "event_queue", None)
    if proc is None:
        return {
            "enabled": False,
            "detail": "Stream consumer not running (set SENSOR_APP_STREAM_CONSUMER_ENABLED=true "
            "and pass event_queue to create_app(), or run run_streaming_stack.py).",
        }
    backlog = eq.qsize() if isinstance(eq, queue.Queue) else None
    snap = proc.status_snapshot(backlog)
    return {"enabled": True, **snap}


async def stream_flush_endpoint(request: Request) -> dict[str, Any]:
    """Drain the in-memory buffer into metric snapshots (does not drain the broker queue)."""
    proc = getattr(request.app.state, "stream_processor", None)
    if proc is None:
        raise HTTPException(status_code=503, detail="stream consumer not configured")
    await asyncio.to_thread(proc.flush)
    return {"ok": True, "detail": "buffer flushed to metric_snapshots"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _llm_features_or_503(request: Request) -> LLMFeatureService:
    backend = getattr(request.app.state, "llm_backend", None)
    if backend is None:
        raise HTTPException(
            status_code=503,
            detail="LLM features disabled or not configured (set SENSOR_APP_LLM_ENABLED and API key)",
        )
    return LLMFeatureService(backend, get_settings(request))


async def _resolve_snapshot(
    store: MetricsStore,
    body: LLMStationWindowBody,
) -> StoredSnapshot:
    if body.snapshot_id is not None:
        snap = await asyncio.to_thread(
            store.get_snapshot_by_id,
            body.station_id,
            body.snapshot_id,
        )
        if snap is None:
            raise HTTPException(status_code=404, detail="snapshot not found for station")
        return snap
    snaps = await asyncio.to_thread(
        store.list_snapshots,
        body.station_id,
        body.start_time,
        body.end_time,
        None,
    )
    if not snaps:
        raise HTTPException(status_code=404, detail="no metric snapshots for station/window")
    return snaps[0]


async def llm_metrics_summary_endpoint(
    request: Request,
    body: LLMStationWindowBody,
) -> LLMTextSummaryResponse:
    """Plain-English operational summary from stored per-device metrics (LLM prose)."""
    store: MetricsStore = request.app.state.store
    snap = await _resolve_snapshot(store, body)
    svc = _llm_features_or_503(request)
    try:
        text, model_used = await svc.natural_language_metrics_summary(
            snap,
            request_id=_request_id(request),
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return LLMTextSummaryResponse(
        summary=text,
        snapshot_id=snap.id,
        model=model_used,
    )


async def llm_data_quality_summary_endpoint(
    request: Request,
    body: LLMStationWindowBody,
) -> LLMTextSummaryResponse:
    """Plain-English data-quality summary grounded in stored ``data_quality`` JSON."""
    store: MetricsStore = request.app.state.store
    snap = await _resolve_snapshot(store, body)
    svc = _llm_features_or_503(request)
    try:
        text, model_used = await svc.natural_language_dq_summary(
            snap,
            request_id=_request_id(request),
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return LLMTextSummaryResponse(
        summary=text,
        snapshot_id=snap.id,
        model=model_used,
    )


async def llm_query_endpoint(
    request: Request,
    body: LLMQueryBody,
) -> LLMQueryResponse:
    """Answer a NL question using an LLM **plan** and Python aggregation over stored metrics."""
    settings_dep = get_settings(request)
    if len(body.question) > settings_dep.llm_max_question_chars:
        raise HTTPException(
            status_code=400,
            detail=f"question exceeds max length ({settings_dep.llm_max_question_chars})",
        )
    store: MetricsStore = request.app.state.store
    snaps = await asyncio.to_thread(
        store.list_snapshots,
        body.station_id,
        body.start_time,
        body.end_time,
        None,
    )
    svc = _llm_features_or_503(request)
    try:
        answer, facts, model_used = await svc.natural_language_query(
            body.question,
            snaps,
            request_id=_request_id(request),
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return LLMQueryResponse(answer=answer, facts=facts, model=model_used)


def _mount_routes(app: FastAPI, settings: Settings) -> None:
    """Register HTTP routes with **slowapi** limits from **settings**."""
    lim = cast(Limiter, app.state.limiter)
    app.get("/")(lim.limit(settings.rate_limit_root)(root_endpoint))
    app.get("/health")(lim.limit(settings.rate_limit_health)(health_endpoint))
    app.post("/process/{station_id}", response_model=ProcessResponse)(
        lim.limit(settings.rate_limit_process)(process_station_endpoint)
    )
    app.get("/metrics/{station_id}")(lim.limit(settings.rate_limit_metrics)(get_metrics_endpoint))
    app.get("/stream/status")(lim.limit(settings.rate_limit_stream_status)(stream_status_endpoint))
    app.post("/stream/flush")(lim.limit(settings.rate_limit_stream_flush)(stream_flush_endpoint))
    app.post("/llm/metrics-summary", response_model=LLMTextSummaryResponse)(
        lim.limit(settings.rate_limit_llm_summary)(llm_metrics_summary_endpoint)
    )
    app.post("/llm/data-quality-summary", response_model=LLMTextSummaryResponse)(
        lim.limit(settings.rate_limit_llm_dq)(llm_data_quality_summary_endpoint)
    )
    app.post("/llm/query", response_model=LLMQueryResponse)(
        lim.limit(settings.rate_limit_llm_query)(llm_query_endpoint)
    )


def create_app(
    settings: Settings | None = None,
    *,
    llm_backend_override: LLMBackend | None = None,
    event_queue: queue.Queue[Any] | None = None,
) -> FastAPI:
    """Build FastAPI app; inject **settings** in tests.

    The default module-level ``app`` uses environment-backed :class:`Settings`.

    Wiring order: limiter + exception handler → settings → routes → request-id middleware
    (middleware added **last** so it runs **first** on the way in).
    """
    configure_app_logging()
    s = settings or Settings()
    app = FastAPI(title="sensor_app", version="0.1.0", lifespan=_lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.state.settings = s
    app.state.llm_backend_override = llm_backend_override
    app.state.event_queue = event_queue
    _mount_routes(app, s)
    app.add_middleware(RequestIdMiddleware)
    return app


app = create_app()
