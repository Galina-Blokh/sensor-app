"""Allowlisted metric keys matching :mod:`sensor_app.lib.metrics_compute` device ``values``."""

from __future__ import annotations

# Keep in sync with keys in ``DeviceMetrics.values`` built in ``compute_station_metrics``.
DEVICE_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "uptime_seconds",
        "average_pressure_bar",
        "peak_pressure_bar",
        "specific_power_kw_per_m3h",
        "cycle_count",
        "total_flow_volume_m3",
        "thermal_efficiency_temp_per_kw",
        "reading_count",
        "pressure_std_bar",
    }
)

# Aggregations permitted in the NL-query JSON plan (executed in Python, not in SQL).
ALLOWED_QUERY_AGGS: frozenset[str] = frozenset({"mean", "max", "min", "sum", "latest"})
