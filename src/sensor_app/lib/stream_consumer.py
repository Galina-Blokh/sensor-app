"""In-process streaming consumer (``spec_ch3``): queue → buffer → same pipeline as batch.

Thread-safe processor; the FastAPI app runs an asyncio task that blocks on
``queue.Queue.get`` in a worker thread and forwards items here. Transport stays
swappable by substituting another blocking source behind the same task contract.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

import polars as pl

from sensor_app.lib.metrics_store import MetricsStore
from sensor_app.lib.pipeline import PipelineConfig, run_pipeline_on_dataframe
from sensor_app.lib.schema_defs import LoadedSchema
from sensor_app.lib.sensor_event import parse_sensor_reading_event

logger = logging.getLogger(__name__)


class StreamProcessorStats:
    """Counters for ``GET /stream/status`` (updated under :class:`StreamProcessor` lock)."""

    def __init__(self) -> None:
        self.events_received = 0
        self.events_processed_rows = 0
        self.events_malformed = 0
        self.events_duplicate = 0
        self.flush_count = 0
        self.last_error: str | None = None
        self.consumer_running = False


class StreamProcessor:
    """Buffer validated rows, flush through :func:`run_pipeline_on_dataframe`, persist snapshots."""

    def __init__(
        self,
        *,
        store: MetricsStore,
        schema: LoadedSchema,
        pipeline_config: PipelineConfig,
        flush_min_events: int,
        max_seen_event_ids: int,
    ) -> None:
        self._store = store
        self._schema = schema
        self._pipeline_config = pipeline_config
        self._flush_min = max(1, flush_min_events)
        self._max_seen = max(1000, max_seen_event_ids)
        self._lock = threading.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._seen_queue: deque[str] = deque()
        self.stats = StreamProcessorStats()

    def status_snapshot(self, backlog_depth: int | None) -> dict[str, Any]:
        """Thread-safe copy of counters for HTTP status."""
        with self._lock:
            return {
                "consumer_running": self.stats.consumer_running,
                "events_received": self.stats.events_received,
                "events_processed_rows": self.stats.events_processed_rows,
                "events_malformed": self.stats.events_malformed,
                "events_duplicate": self.stats.events_duplicate,
                "flush_count": self.stats.flush_count,
                "buffered_rows_pending": len(self._buffer),
                "last_error": self.stats.last_error,
                "backlog_depth": backlog_depth,
            }

    def set_running(self, value: bool) -> None:
        with self._lock:
            self.stats.consumer_running = value

    def handle(self, item: dict[str, Any] | None) -> None:
        """Process one queue item. ``None`` is the producer end-of-stream sentinel → flush."""
        if item is None:
            self._flush_all("stream_sentinel")
            return

        with self._lock:
            self.stats.events_received += 1  # one dequeued message (before dedupe / parse)
            eid = item.get("event_id")
            if isinstance(eid, str) and eid in self._seen_ids:
                self.stats.events_duplicate += 1
                return

            row = parse_sensor_reading_event(item)
            if row is None:
                self.stats.events_malformed += 1
                logger.info(
                    "stream_malformed_event",
                    extra={"event_id": item.get("event_id", "-")},
                )
                return

            if isinstance(eid, str):
                self._remember_id(eid)

            self._buffer.append(row)
            nbuf = len(self._buffer)
            should_flush = nbuf >= self._flush_min

        if should_flush:
            self._flush_all("buffer_threshold")

    def flush(self) -> None:
        """Public flush (e.g. HTTP); drains buffer."""
        self._flush_all("manual")

    def _remember_id(self, eid: str) -> None:
        """Call only while holding ``_lock``."""
        self._seen_ids.add(eid)
        self._seen_queue.append(eid)
        while len(self._seen_queue) > self._max_seen:
            old = self._seen_queue.popleft()
            self._seen_ids.discard(old)

    def _flush_all(self, reason: str) -> None:
        with self._lock:
            if not self._buffer:
                return
            rows = self._buffer
            self._buffer = []

        try:
            df = pl.DataFrame(rows)
        except Exception as e:
            with self._lock:
                self.stats.last_error = f"dataframe_build: {e}"
            logger.exception("stream_flush_dataframe_failed", extra={"reason": reason})
            return

        computed_at = datetime.now(timezone.utc).isoformat()
        dq_extra = {"ingest": "stream_consumer", "flush_reason": reason}
        try:
            for sid in df["station_id"].unique().to_list():
                sub = df.filter(pl.col("station_id") == sid)
                payload = run_pipeline_on_dataframe(
                    sub,
                    str(sid),
                    self._schema,
                    self._pipeline_config,
                    dq_extra=dq_extra,
                )
                self._store.save_snapshot(payload, computed_at)
                with self._lock:
                    self.stats.events_processed_rows += int(sub.height)
                    self.stats.flush_count += 1
        except Exception as e:
            with self._lock:
                self.stats.last_error = f"pipeline_or_save: {e}"
            logger.exception("stream_flush_pipeline_failed", extra={"reason": reason})
