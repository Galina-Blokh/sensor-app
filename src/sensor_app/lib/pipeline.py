"""End-to-end ingestion: load → validate → clean → resample → DQ report → metrics.

**Stages**

1. **Load** — SQLite (or any :class:`~sensor_app.lib.repository.SensorReadingSource`)
   into a Polars frame with typed columns.
2. **Validate** — Schema-driven: out-of-range numerics are nulled; counts feed DQ.
3. **Impute** — Per ``device_id``: drop / forward-fill / interpolate (configurable).
4. **Resample** — ``group_by_dynamic`` mean on a fixed grid (e.g. 5 min).
5. **DQ** — Missing fractions, OOR counts, flatline run detection (numpy scan).
6. **Metrics** — :mod:`sensor_app.lib.metrics_compute` on the resampled frame.

Columnar work uses **Polars** (``group_by_dynamic``, ``map_groups``, vectorized
expressions). Optional **lazy** scans can be added later for larger-than-RAM data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np
import polars as pl

from sensor_app.lib.metrics_compute import StationMetricsResult, compute_station_metrics
from sensor_app.lib.repository import SensorReadingSource, SqliteSensorRepository
from sensor_app.lib.schema_defs import LoadedSchema


class MissingDataStrategy(str, Enum):
    """Post-validation null repair strategy (per **device_id**)."""

    DROP = "drop"
    FFILL = "ffill"
    INTERPOLATE = "interpolate"


@dataclass
class PipelineConfig:
    """User-tunable pipeline knobs (also mapped from :class:`sensor_app.settings.Settings`).

    ``resample_rule`` uses pandas-style tokens where applicable (``min`` → Polars ``m``).
    """

    resample_rule: str = "5min"
    missing_strategy: MissingDataStrategy = MissingDataStrategy.INTERPOLATE
    motor_on_threshold_rpm: float = 1.0
    power_on_threshold_kw: float = 0.5
    flow_epsilon_m3h: float = 1e-3


@dataclass
class DataQualityReport:
    """Serializable DQ summary stored next to metric snapshots.

    ``missing_fraction_by_column`` is computed **after** clean/resample. ``extra`` holds
    pre-clean hints (e.g. flatline count before resampling) for debugging.
    """

    missing_fraction_by_column: dict[str, float]
    out_of_range_count_by_column: dict[str, int]
    flatline_periods_count: int
    rows_before_clean: int
    rows_after_clean: int
    anomaly_rows_pre_clean: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Flatten for JSON persistence (API / ``metric_snapshots``)."""
        return asdict(self)


def _pandas_style_rule_to_polars(rule: str) -> str:
    """Translate ``5min``-style offsets to Polars duration tokens (``5m``)."""
    r = rule.strip()
    if r.endswith("min"):
        return r[:-3] + "m"
    return r


def _validate_ranges(df: pl.DataFrame, schema: LoadedSchema) -> tuple[pl.DataFrame, dict[str, int]]:
    """Null out-of-range values; return violation counts per numeric column."""
    counts: dict[str, int] = dict.fromkeys(schema.sensor_numeric_columns, 0)
    out = df
    for col in schema.sensor_numeric_columns:
        spec = next((c for c in schema.reading_columns if c.name == col), None)
        if spec is None or spec.valid_range is None:
            continue
        lo, hi = spec.valid_range
        lo_f, hi_f = float(lo), float(hi)
        c64 = pl.col(col).cast(pl.Float64)
        bad = c64.is_not_null() & ((c64 < lo_f) | (c64 > hi_f))
        counts[col] = int(out.filter(bad).height)
        out = out.with_columns(pl.when(bad).then(None).otherwise(pl.col(col)).alias(col))
    return out, counts


def _missing_fractions(df: pl.DataFrame, cols: list[str]) -> dict[str, float]:
    """Fraction of nulls per column (0 if empty frame)."""
    n = df.height
    if n == 0:
        return dict.fromkeys(cols, 0.0)
    out: dict[str, float] = {}
    for c in cols:
        if c not in df.columns:
            continue
        out[c] = float(df.select(pl.col(c).null_count()).item()) / float(n)
    return out


def _flatline_runs_exceeding_threshold(
    t_ns: np.ndarray,
    vals: np.ndarray,
    thr_min: int,
) -> int:
    """Count runs of identical non-null values (length ≥2) lasting at least **thr_min** minutes."""
    n = len(vals)
    if n < 2:
        return 0
    total = 0
    i = 0
    while i < n:
        if np.isnan(vals[i]):
            i += 1
            continue
        j = i + 1
        while j < n and vals[j] == vals[i] and not np.isnan(vals[j]):
            j += 1
        if j - i >= 2:
            dur_min = (float(t_ns[j - 1]) - float(t_ns[i])) / 1e9 / 60.0
            if dur_min >= thr_min:
                total += 1
        i = j
    return total


def _count_flatline_periods(df: pl.DataFrame, schema: LoadedSchema) -> int:
    """Runs of identical non-null readings longer than schema flatline minutes."""
    if df.is_empty():
        return 0
    total = 0
    ordered = df.sort("device_id", "timestamp")
    for g in ordered.partition_by("device_id", maintain_order=True):
        t_ns = g.select(pl.col("timestamp").dt.timestamp("ns").alias("_t"))["_t"].to_numpy()
        for col in schema.sensor_numeric_columns:
            if col not in g.columns:
                continue
            thr_min = schema.flatline_threshold_minutes.get(col)
            if thr_min is None:
                continue
            vals = g.get_column(col).cast(pl.Float64).to_numpy()
            total += _flatline_runs_exceeding_threshold(t_ns, vals, thr_min)
    return total


def _apply_missing_strategy(
    df: pl.DataFrame,
    strategy: MissingDataStrategy,
    numeric_cols: list[str],
) -> pl.DataFrame:
    """Sort per device; drop nulls, forward-fill, or interpolate numerics."""

    def _per_group(g: pl.DataFrame) -> pl.DataFrame:
        g = g.sort("timestamp")
        nums = [c for c in numeric_cols if c in g.columns]
        if not nums:
            return g
        if strategy is MissingDataStrategy.DROP:
            return g.drop_nulls(subset=nums)
        if strategy is MissingDataStrategy.FFILL:
            return g.with_columns([pl.col(c).forward_fill() for c in nums])
        return g.with_columns([pl.col(c).interpolate() for c in nums])

    if df.is_empty():
        return df
    return (
        df.sort("device_id", "timestamp")
        .group_by("device_id", maintain_order=True)
        .map_groups(_per_group)
    )


def _resample_by_device(df: pl.DataFrame, rule: str, numeric_cols: list[str]) -> pl.DataFrame:
    """Mean-aggregate **numeric_cols** on a uniform grid per **device_id**."""
    if df.is_empty():
        return df
    every = _pandas_style_rule_to_polars(rule)
    nums = [c for c in numeric_cols if c in df.columns]
    if not nums:
        return df
    agg_exprs: list[pl.Expr] = [pl.col(c).mean().alias(c) for c in nums]
    agg_exprs.append(pl.col("station_id").first())
    return (
        df.sort("device_id", "timestamp")
        .group_by_dynamic(
            index_column="timestamp",
            every=every,
            closed="left",
            group_by="device_id",
        )
        .agg(agg_exprs)
    )


def _data_quality_score(missing: dict[str, float], oor: dict[str, int], rows: int) -> float:
    """Heuristic 0–100 score: higher is better; blends null rate and OOR row rate."""
    if rows == 0:
        return 100.0
    avg_miss = sum(missing.values()) / max(len(missing), 1)
    oor_rate = sum(oor.values()) / float(rows)
    penalty = min(1.0, avg_miss * 0.5 + oor_rate * 0.5)
    return round(100.0 * (1.0 - penalty), 2)


def _any_oor_expr(numeric_cols: list[str], schema: LoadedSchema) -> pl.Expr:
    """Row-level OR of out-of-range conditions (pre-nulling, use on **raw** before validate)."""
    parts: list[pl.Expr] = []
    for col in numeric_cols:
        spec = next((c for c in schema.reading_columns if c.name == col), None)
        if spec is None or spec.valid_range is None:
            continue
        lo, hi = spec.valid_range
        lo_f, hi_f = float(lo), float(hi)
        c64 = pl.col(col).cast(pl.Float64)
        parts.append(c64.is_not_null() & ((c64 < lo_f) | (c64 > hi_f)))
    if not parts:
        return pl.lit(False)
    out = parts[0]
    for p in parts[1:]:
        out = out | p
    return out


def run_station_pipeline(
    sensor_db_path: str,
    schema_path: str,
    station_id: str,
    start: datetime | None,
    end: datetime | None,
    config: PipelineConfig | None = None,
) -> dict[str, Any]:
    """Execute load → validate → impute → resample → metrics; return JSON-friendly dict.

    Args:
        sensor_db_path: SQLite file for :class:`~sensor_app.lib.repository.SqliteSensorRepository`.
        schema_path: JSON schema (columns + flatline thresholds).
        station_id: Station filter for the query.
        start: Inclusive window start (UTC), or ``None`` for open start.
        end: Inclusive window end (UTC), or ``None`` for open end.
        config: Pipeline tuning; defaults match :class:`PipelineConfig`.

    Returns:
        Dict with ``station_id``, ``window_*``, ``data_quality``, ``data_quality_score``,
        and ``metrics`` (nested devices + extras). Safe to pass to
        :meth:`sensor_app.lib.metrics_store.MetricsStore.save_snapshot`.

    Raises:
        FileNotFoundError: If **schema_path** does not exist.
    """
    cfg = config or PipelineConfig()
    schema = LoadedSchema.from_path(schema_path)
    repo: SensorReadingSource = SqliteSensorRepository(sensor_db_path)
    raw = repo.load_readings(station_id, start, end)
    rows_before = raw.height
    # Short-circuit: still return a stable shape so the API can persist an empty snapshot.
    if raw.is_empty():
        dq = DataQualityReport(
            missing_fraction_by_column={},
            out_of_range_count_by_column={},
            flatline_periods_count=0,
            rows_before_clean=0,
            rows_after_clean=0,
            anomaly_rows_pre_clean=0,
            extra={"note": "no rows for station/window"},
        )
        metrics = compute_station_metrics(
            raw,
            station_id,
            cfg.motor_on_threshold_rpm,
            cfg.power_on_threshold_kw,
            cfg.flow_epsilon_m3h,
        )
        return {
            "station_id": station_id,
            "window_start": "",
            "window_end": "",
            "data_quality": dq.to_dict(),
            "data_quality_score": 0.0,
            "metrics": _metrics_to_json(metrics),
        }

    numeric_cols = schema.sensor_numeric_columns
    miss_before = _missing_fractions(raw, ["timestamp", "station_id", "device_id", *numeric_cols])
    # Pre-clean: how many rows had at least one OOR numeric (before nulling).
    bad_raw = _any_oor_expr(numeric_cols, schema)
    anomaly_rows = int(raw.filter(bad_raw).height)

    validated, oor_counts = _validate_ranges(raw, schema)
    flat_pre = _count_flatline_periods(validated, schema)
    cleaned = _apply_missing_strategy(validated, cfg.missing_strategy, numeric_cols)
    resampled = _resample_by_device(cleaned, cfg.resample_rule, numeric_cols)
    flat_post = _count_flatline_periods(resampled, schema)
    miss_after = _missing_fractions(resampled, numeric_cols)

    dq = DataQualityReport(
        missing_fraction_by_column=miss_after,
        out_of_range_count_by_column=oor_counts,
        flatline_periods_count=flat_post,
        rows_before_clean=rows_before,
        rows_after_clean=resampled.height,
        anomaly_rows_pre_clean=anomaly_rows,
        extra={
            "missing_fraction_before_clean": miss_before,
            "flatline_periods_pre_clean_hint": flat_pre,
            "resample_rule": cfg.resample_rule,
            "missing_strategy": cfg.missing_strategy.value,
        },
    )
    score = _data_quality_score(miss_after, oor_counts, max(rows_before, 1))

    metrics = compute_station_metrics(
        resampled,
        station_id,
        cfg.motor_on_threshold_rpm,
        cfg.power_on_threshold_kw,
        cfg.flow_epsilon_m3h,
    )
    metrics.extras["data_quality_score"] = score

    return {
        "station_id": station_id,
        "window_start": metrics.window_start,
        "window_end": metrics.window_end,
        "data_quality": dq.to_dict(),
        "data_quality_score": score,
        "metrics": _metrics_to_json(metrics),
    }


def _metrics_to_json(m: StationMetricsResult) -> dict[str, Any]:
    """Normalize :class:`~sensor_app.lib.metrics_compute.StationMetricsResult` for JSON/API."""
    assert isinstance(m, StationMetricsResult)
    return {
        "station_id": m.station_id,
        "window_start": m.window_start,
        "window_end": m.window_end,
        "devices": [{"device_id": d.device_id, "metrics": d.values} for d in m.devices],
        "extras": m.extras,
    }
