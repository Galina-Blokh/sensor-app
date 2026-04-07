"""Operational metrics from cleaned time-series (**Polars**).

Assumes **sensor_readings** column names and a UTC **timestamp**. Per-device
blocks are processed with vectorized expressions where possible.

**Metric definitions (high level)**

* **Uptime** — Sum of time deltas where the motor *or* power reading indicates
  the unit was *on* in two consecutive samples (avoids counting single spikes).
* **Cycles** — Count of off→on transitions using the same on/off rule.
* **Flow volume** — Trapezoidal integration of air flow (m³/h) over elapsed time.
* **Specific power** — Mean of ``power / flow`` only where ``|flow| > flow_eps``
  and the ratio is finite (guards divide-by-zero).
* **Thermal efficiency** — Heuristic ``mean(temp | power on) / mean(power | power on)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl


def _series_float(df: pl.DataFrame, expr: pl.Expr) -> float:
    """Single scalar from a one-cell Polars frame (keeps mypy happy vs ``Series`` unions)."""
    return float(df.select(expr).item())


@dataclass
class DeviceMetrics:
    """JSON-serializable metrics for one compressor **device_id**.

    ``values`` keys match the API contract (snake_case, physical units in names).
    """

    device_id: str
    values: dict[str, float | int]


@dataclass
class StationMetricsResult:
    """All devices for one station and the time window spanned by **df**.

    ``extras`` can carry cross-cutting fields (e.g. pipeline-injected DQ score).
    """

    station_id: str
    window_start: str
    window_end: str
    devices: list[DeviceMetrics]
    extras: dict[str, Any] = field(default_factory=dict)


def _uptime_seconds(
    g: pl.DataFrame, motor_col: str, power_col: str, mthr: float, pthr: float
) -> float:
    """Sum Δt (seconds) where consecutive samples are both *on*.

    A sample is *on* if motor > **mthr** OR power > **pthr**. Only intervals where
    both the current and previous row are *on* contribute (piecewise-constant hold).
    """
    g = g.sort("timestamp")
    if g.height < 2:
        return 0.0
    out = g.with_columns(
        pl.col("timestamp").diff().dt.total_seconds().alias("dt"),
        ((pl.col(motor_col).fill_null(0) > mthr) | (pl.col(power_col).fill_null(0) > pthr)).alias(
            "active"
        ),
    ).with_columns(
        (pl.col("active") & pl.col("active").shift(1).fill_null(False)).alias("both"),
    )
    return _series_float(out, (pl.col("dt") * pl.col("both").cast(pl.Float64)).sum().alias("s"))


def _cycle_count(g: pl.DataFrame, motor_col: str, power_col: str, mthr: float, pthr: float) -> int:
    """Count transitions from *off* to *on* (rising edges of the combined on signal)."""
    g = g.sort("timestamp")
    if g.is_empty():
        return 0
    active = (pl.col(motor_col).fill_null(0) > mthr) | (pl.col(power_col).fill_null(0) > pthr)
    inactive_prev = ~active.shift(1).fill_null(False)
    turn_on = active & inactive_prev
    return int(_series_float(g, turn_on.sum().alias("n")))


def _total_flow_volume_m3(g: pl.DataFrame, flow_col: str) -> float:
    """Trapezoidal volume (m³) from flow in m³/h and wall-clock spacing.

    First row has no predecessor → skipped in the sum (``dvol[1:]``).
    """
    g = g.sort("timestamp")
    if g.height < 2:
        return 0.0
    out = g.with_columns(
        pl.col("timestamp").diff().dt.total_seconds().alias("dt_sec"),
        pl.col(flow_col).cast(pl.Float64).alias("_flow"),
    ).with_columns(
        ((pl.col("_flow") + pl.col("_flow").shift(1)) / 2.0 * (pl.col("dt_sec") / 3600.0)).alias(
            "dvol"
        )
    )
    # Row 0 has null ``dt_sec`` / undefined trapezoid; ``slice(1)`` matches ``dvol[1:]``.
    return _series_float(out, pl.col("dvol").slice(1).sum())


def compute_station_metrics(
    df: pl.DataFrame,
    station_id: str,
    motor_thr: float,
    power_thr: float,
    flow_eps: float,
) -> StationMetricsResult:
    """Compute baseline + extension metrics per **device_id** in **df**.

    Args:
        df: Cleaned/resampled readings (may be empty).
        station_id: Logical station (echoed in the result).
        motor_thr: RPM threshold for *on* (see :func:`_uptime_seconds`).
        power_thr: kW threshold for *on*.
        flow_eps: Minimum |flow| (m³/h) for specific-power averaging.

    Returns:
        :class:`StationMetricsResult` with ISO window bounds from min/max timestamp.
    """
    if df.is_empty():
        return StationMetricsResult(
            station_id=station_id,
            window_start="",
            window_end="",
            devices=[],
            extras={},
        )
    ts_col = "timestamp"
    w_start = df.select(pl.col(ts_col).min()).item()
    w_end = df.select(pl.col(ts_col).max()).item()
    pressure = "discharge_pressure"
    flow = "air_flow_rate"
    power = "power_consumption"
    motor = "motor_speed"
    temp = "discharge_temp"

    devices_out: list[DeviceMetrics] = []
    for g in df.sort("device_id", "timestamp").partition_by("device_id", maintain_order=True):
        uptime_s = _uptime_seconds(g, motor, power, motor_thr, power_thr)
        avg_p = _series_float(g, pl.col(pressure).mean()) if pressure in g.columns else float("nan")
        peak_p = _series_float(g, pl.col(pressure).max()) if pressure in g.columns else float("nan")

        # Specific power: only trustworthy when flow magnitude is meaningful.
        gf = g.with_columns(
            (pl.col(power).cast(pl.Float64) / pl.col(flow).cast(pl.Float64)).alias("sp")
        ).filter(pl.col(flow).abs() > flow_eps)
        gf = gf.filter(pl.col("sp").is_finite())
        spec_power = _series_float(gf, pl.col("sp").mean()) if gf.height > 0 else float("nan")

        cycles = _cycle_count(g, motor, power, motor_thr, power_thr)
        vol = _total_flow_volume_m3(g, flow)

        on = g.filter(pl.col(power).cast(pl.Float64) > power_thr)
        if on.height > 0:
            # Ratio of conditional means (not mean of ratios).
            thermal = _series_float(on, pl.col(temp).mean() / pl.col(power).mean())
        else:
            thermal = float("nan")

        reading_count = int(g.height)
        p_std = _series_float(g, pl.col(pressure).std()) if pressure in g.columns else float("nan")

        vals: dict[str, float | int] = {
            "uptime_seconds": int(round(uptime_s)),
            "average_pressure_bar": avg_p,
            "peak_pressure_bar": peak_p,
            "specific_power_kw_per_m3h": spec_power,
            "cycle_count": cycles,
            "total_flow_volume_m3": vol,
            "thermal_efficiency_temp_per_kw": thermal,
            "reading_count": reading_count,
            "pressure_std_bar": p_std,
        }
        did = str(g.get_column("device_id")[0])
        devices_out.append(DeviceMetrics(device_id=did, values=vals))

    # ``min``/``max`` on a Datetime column yield Python ``datetime`` in practice.
    ws = w_start.isoformat() if isinstance(w_start, datetime) else str(w_start)
    we = w_end.isoformat() if isinstance(w_end, datetime) else str(w_end)
    return StationMetricsResult(
        station_id=station_id,
        window_start=ws,
        window_end=we,
        devices=devices_out,
        extras={"rows_processed": int(df.height)},
    )
