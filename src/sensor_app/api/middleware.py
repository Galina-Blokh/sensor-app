"""HTTP middleware and logging helpers for **request correlation**.

``X-Request-ID`` is accepted from the client or generated (UUID4). The same value
is attached to ``request.state``, stored in a :class:`contextvars.ContextVar` for
the async call chain, echoed on the response, and injected into log records via
:class:`RequestIdLogFilter` so plain ``logging`` output stays grep-friendly.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"

# Propagates through ``async`` handlers on the same task; ``asyncio.to_thread`` copies
# context in Python 3.11+, so worker logs may still see an ID when supported.
request_id_ctx: ContextVar[str | None] = ContextVar("sensor_app_request_id", default=None)


def get_request_id() -> str | None:
    """Return the active request id for this task, if any."""
    return request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign or forward ``X-Request-ID`` and bind it for the duration of the request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())
        request.state.request_id = rid
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class RequestIdLogFilter(logging.Filter):
    """Ensure every record has ``request_id`` for formatters (``-`` when outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            rid = request_id_ctx.get()
            record.request_id = rid if rid is not None else "-"
        return True


_logging_configured = False


def configure_app_logging() -> None:
    """Attach a single stream handler + structured-ish formatter for ``sensor_app`` loggers.

    Idempotent: safe to call from :func:`sensor_app.api.main.create_app` once per process.
    Uses ``propagate=False`` so we do not duplicate lines under uvicorn's root logging.
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    root = logging.getLogger("sensor_app")
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdLogFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | request_id=%(request_id)s | %(message)s"
        )
    )
    root.addHandler(handler)
    root.propagate = False
