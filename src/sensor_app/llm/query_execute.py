"""Execute validated query plans over stored metrics in Python (structured access only)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sensor_app.lib.metrics_store import StoredSnapshot
from sensor_app.llm.allowed_keys import ALLOWED_QUERY_AGGS, DEVICE_METRIC_KEYS


@dataclass(frozen=True, slots=True)
class QueryPlan:
    metric_key: str
    agg: str
    device_id: str | None


def parse_and_validate_plan(data: dict[str, Any]) -> QueryPlan:
    mk = data.get("metric_key")
    if not isinstance(mk, str) or mk not in DEVICE_METRIC_KEYS:
        raise ValueError("invalid or missing metric_key")
    agg = data.get("agg")
    if not isinstance(agg, str) or agg not in ALLOWED_QUERY_AGGS:
        raise ValueError("invalid or missing agg")
    dev = data.get("device_id")
    if dev is not None and not isinstance(dev, str):
        raise ValueError("device_id must be string or null")
    if dev == "":
        dev = None
    return QueryPlan(metric_key=mk, agg=agg, device_id=dev)


def _float_vals(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return float(x)
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return None


def collect_numeric_series(
    snaps: list[StoredSnapshot],
    metric_key: str,
    device_id: str | None,
) -> list[float]:
    """Gather scalar values across snapshots (and optionally one device)."""
    out: list[float] = []
    for snap in snaps:
        devs = snap.metrics.get("devices", [])
        if not isinstance(devs, list):
            continue
        for d in devs:
            if not isinstance(d, dict):
                continue
            did = d.get("device_id")
            if device_id is not None and str(did) != device_id:
                continue
            # Pipeline persists device scalars as ``metrics`` (see ``_metrics_to_json``).
            vals = d.get("metrics")
            if not isinstance(vals, dict):
                vals = d.get("values")
            if not isinstance(vals, dict):
                continue
            v = _float_vals(vals.get(metric_key))
            if v is not None:
                out.append(v)
    return out


def aggregate_values(values: list[float], agg: str) -> float | None:
    if not values:
        return None
    if agg == "latest":
        return values[0]
    if agg == "mean":
        return sum(values) / len(values)
    if agg == "sum":
        return sum(values)
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    return None


def run_query_plan(snaps: list[StoredSnapshot], plan: QueryPlan) -> dict[str, Any]:
    """Apply **plan** to **snaps** (newest-first). ``latest`` uses only the newest snapshot."""
    if plan.agg == "latest":
        if not snaps:
            return {
                "metric_key": plan.metric_key,
                "agg": plan.agg,
                "device_id": plan.device_id,
                "value": None,
                "n_values": 0,
                "snapshot_ids_used": [],
            }
        series = collect_numeric_series([snaps[0]], plan.metric_key, plan.device_id)
        if not series:
            val = None
        elif len(series) == 1:
            val = series[0]
        else:
            val = aggregate_values(series, "mean")
        return {
            "metric_key": plan.metric_key,
            "agg": plan.agg,
            "device_id": plan.device_id,
            "value": val,
            "n_values": len(series),
            "snapshot_ids_used": [snaps[0].id],
        }
    series = collect_numeric_series(snaps, plan.metric_key, plan.device_id)
    val = aggregate_values(series, plan.agg)
    return {
        "metric_key": plan.metric_key,
        "agg": plan.agg,
        "device_id": plan.device_id,
        "value": val,
        "n_values": len(series),
        "snapshot_ids_used": [s.id for s in snaps],
    }


def format_query_answer(facts: dict[str, Any]) -> str:
    """Deterministic plain sentence from **facts** (no second LLM call)."""
    v = facts.get("value")
    mk = facts.get("metric_key")
    agg = facts.get("agg")
    n = facts.get("n_values")
    if v is None:
        return f"No numeric values found for {mk} with aggregation {agg!r} (n={n})."
    try:
        vf = float(v)
        if abs(vf) >= 1e4 or (abs(vf) > 0 and abs(vf) < 1e-4):
            num = f"{vf:.6g}"
        else:
            num = f"{vf:.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        num = str(v)
    dev = facts.get("device_id")
    dev_s = f" for device {dev!r}" if dev else " across matching devices"
    return f"{agg} {mk}{dev_s}: {num} (from {n} series values)."
