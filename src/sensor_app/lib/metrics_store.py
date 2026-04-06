"""**Persistence** layer for computed metric snapshots (SQLite).

Each successful ``POST /process`` produces one row in ``metric_snapshots`` with
JSON blobs for data-quality and per-device metrics. Re-processing the same
``(station_id, window_start, window_end)`` **replaces** the row (idempotent key).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class StoredSnapshot:
    """One row from ``metric_snapshots`` after JSON parsing."""

    id: int
    station_id: str
    window_start: str | None
    window_end: str | None
    computed_at: str
    data_quality: dict[str, Any]
    metrics: dict[str, Any]


class MetricsStore:
    """Small SQLite-backed store; suitable for demos — use a real TS/OLAP DB at scale."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def init(self) -> None:
        """Create ``metric_snapshots`` if missing (idempotent)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL,
                    window_start TEXT,
                    window_end TEXT,
                    computed_at TEXT NOT NULL,
                    data_quality_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    UNIQUE (station_id, window_start, window_end)
                )
                """
            )
            conn.commit()

    def save_snapshot(self, payload: dict[str, Any], computed_at_iso: str) -> int:
        """Upsert by logical window key; return SQLite ``rowid`` of the insert.

        Args:
            payload: Output of :func:`sensor_app.lib.pipeline.run_station_pipeline`.
            computed_at_iso: Server UTC timestamp when the snapshot was taken.
        """
        station_id = str(payload["station_id"])
        # Empty string sentinel matches UNIQUE semantics (NULL would not dedupe in SQLite).
        window_start = payload.get("window_start") or ""
        window_end = payload.get("window_end") or ""
        dq = payload["data_quality"]
        metrics = payload["metrics"]
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM metric_snapshots WHERE station_id = ? "
                "AND window_start = ? AND window_end = ?",
                (station_id, window_start, window_end),
            )
            cur = conn.execute(
                """
                INSERT INTO metric_snapshots
                (station_id, window_start, window_end, computed_at, data_quality_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    station_id,
                    window_start,
                    window_end,
                    computed_at_iso,
                    json.dumps(dq),
                    json.dumps(metrics),
                ),
            )
            conn.commit()
            # ``lastrowid`` is always set after a successful INSERT; stubs allow None.
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError("SQLite INSERT did not set lastrowid")
            return int(row_id)

    def list_snapshots(
        self,
        station_id: str,
        start: str | None = None,
        end: str | None = None,
        device_id: str | None = None,
    ) -> list[StoredSnapshot]:
        """Return newest-first snapshots, optional overlap filter on window strings.

        If **device_id** is set, the ``metrics["devices"]`` list is filtered client-side
        (full snapshot row is still one DB row).
        """
        q = (
            "SELECT id, station_id, window_start, window_end, computed_at, "
            "data_quality_json, metrics_json FROM metric_snapshots WHERE station_id = ?"
        )
        params: list[Any] = [station_id]
        if start:
            q += " AND (window_end = '' OR window_end >= ?)"
            params.append(start)
        if end:
            q += " AND (window_start = '' OR window_start <= ?)"
            params.append(end)
        q += " ORDER BY computed_at DESC"
        out: list[StoredSnapshot] = []
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(q, params):
                metrics = json.loads(row["metrics_json"])
                if device_id:
                    devs = metrics.get("devices", [])
                    metrics = {
                        **metrics,
                        "devices": [d for d in devs if d.get("device_id") == device_id],
                    }
                out.append(
                    StoredSnapshot(
                        id=int(row["id"]),
                        station_id=row["station_id"],
                        window_start=row["window_start"],
                        window_end=row["window_end"],
                        computed_at=row["computed_at"],
                        data_quality=json.loads(row["data_quality_json"]),
                        metrics=metrics,
                    )
                )
        return out
