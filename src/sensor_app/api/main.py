"""Async FastAPI entrypoint.

**Polars** / SQLite CPU and blocking I/O run in worker threads via
:class:`asyncio.to_thread` so request handlers stay non-blocking.

**Cross-cutting:** :class:`~sensor_app.api.middleware.RequestIdMiddleware` (``X-Request-ID``)
and **slowapi** rate limits per route (see :class:`sensor_app.settings.Settings`).

Routes: ``GET /`` (index), ``GET /health``, ``POST /process/{station_id}``,
``GET /metrics/{station_id}`` (plus FastAPI ``/docs``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from sensor_app.api.middleware import RequestIdMiddleware, configure_app_logging
from sensor_app.lib.metrics_store import MetricsStore
from sensor_app.lib.pipeline import MissingDataStrategy, PipelineConfig, run_station_pipeline
from sensor_app.settings import Settings

logger = logging.getLogger(__name__)

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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure ``metric_snapshots`` exists before serving; log paths once."""
    settings: Settings = app.state.settings
    store = MetricsStore(settings.metrics_db_path)
    store.init()
    app.state.store = store
    logger.info(
        "sensor_app started",
        extra={
            "sensor_db": settings.sensor_db_path,
            "metrics_db": settings.metrics_db_path,
        },
    )
    yield


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
) -> list[dict[str, Any]]:
    """List saved snapshots for **station_id**, newest first; optional window/device filter."""
    store: MetricsStore = request.app.state.store
    snaps = await asyncio.to_thread(
        store.list_snapshots,
        station_id,
        start_time,
        end_time,
        device_id,
    )
    return [
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


def _mount_routes(app: FastAPI, settings: Settings) -> None:
    """Register HTTP routes with **slowapi** limits from **settings**."""
    lim = cast(Limiter, app.state.limiter)
    app.get("/")(lim.limit(settings.rate_limit_root)(root_endpoint))
    app.get("/health")(lim.limit(settings.rate_limit_health)(health_endpoint))
    app.post("/process/{station_id}", response_model=ProcessResponse)(
        lim.limit(settings.rate_limit_process)(process_station_endpoint)
    )
    app.get("/metrics/{station_id}")(lim.limit(settings.rate_limit_metrics)(get_metrics_endpoint))


def create_app(settings: Settings | None = None) -> FastAPI:
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
    _mount_routes(app, s)
    app.add_middleware(RequestIdMiddleware)
    return app


app = create_app()
