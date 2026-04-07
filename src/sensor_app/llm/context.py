"""Build bounded JSON context from stored snapshots (no SQL from user or model text)."""

from __future__ import annotations

import json
from typing import Any

from sensor_app.lib.metrics_store import StoredSnapshot


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def snapshot_metrics_context(snap: StoredSnapshot) -> dict[str, Any]:
    """Structured view of one row: station, window, devices[].values only.

    Persisted snapshots use ``devices[].metrics`` (see pipeline); we normalize to
    ``values`` in this payload so prompts stay stable.
    """
    devices = snap.metrics.get("devices", [])
    slim = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        did = d.get("device_id")
        vals = d.get("metrics")
        if not isinstance(vals, dict):
            vals = d.get("values")
        if isinstance(vals, dict):
            slim.append({"device_id": did, "values": vals})
    return {
        "snapshot_id": snap.id,
        "station_id": snap.station_id,
        "window_start": snap.window_start,
        "window_end": snap.window_end,
        "computed_at": snap.computed_at,
        "devices": slim,
        "extras": snap.metrics.get("extras", {}),
    }


def snapshot_dq_context(snap: StoredSnapshot) -> dict[str, Any]:
    """Data-quality blob as persisted (spec_ch1 pipeline output)."""
    return {
        "snapshot_id": snap.id,
        "station_id": snap.station_id,
        "window_start": snap.window_start,
        "window_end": snap.window_end,
        "computed_at": snap.computed_at,
        "data_quality": snap.data_quality,
        "data_quality_score": snap.metrics.get("data_quality_score"),
    }


def dumps_bounded(obj: Any, max_chars: int) -> str:
    """JSON-serialize then truncate by character count (UTF-8 safe for ASCII metrics)."""
    raw = json.dumps(obj, separators=(",", ":"), default=str)
    return _truncate(raw, max_chars)


def multi_snapshot_query_context(snaps: list[StoredSnapshot]) -> list[dict[str, Any]]:
    """One entry per snapshot, newest-first order preserved.

    Includes ``data_quality`` so NL queries about missing/out-of-range data can be
    grounded; aggregation still runs only on allowlisted ``metric_key`` values.
    """
    out: list[dict[str, Any]] = []
    for s in snaps:
        row = snapshot_metrics_context(s)
        row["data_quality"] = s.data_quality
        extras = s.metrics.get("extras")
        row["data_quality_score"] = (
            extras.get("data_quality_score") if isinstance(extras, dict) else None
        )
        out.append(row)
    return out
