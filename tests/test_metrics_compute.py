"""Unit: metric formulas on synthetic Polars frames."""

from __future__ import annotations

import polars as pl

from sensor_app.lib.metrics_compute import compute_station_metrics


def test_compute_basic_device() -> None:
    ts = [
        "2024-01-01T00:00:00Z",
        "2024-01-01T01:00:00Z",
        "2024-01-01T02:00:00Z",
        "2024-01-01T03:00:00Z",
        "2024-01-01T04:00:00Z",
    ]
    df = pl.DataFrame(
        {
            "timestamp": pl.Series(ts).str.to_datetime(time_zone="UTC"),
            "station_id": ["s1"] * 5,
            "device_id": ["d1"] * 5,
            "discharge_pressure": [7.0, 7.1, 7.2, 7.0, 7.0],
            "air_flow_rate": [100.0, 100.0, 0.0, 100.0, 100.0],
            "power_consumption": [50.0, 50.0, 0.0, 50.0, 50.0],
            "motor_speed": [1500.0, 1500.0, 0.0, 1500.0, 1500.0],
            "discharge_temp": [40.0, 41.0, 25.0, 40.0, 40.0],
        }
    )
    r = compute_station_metrics(df, "s1", 1.0, 0.5, 1e-3)
    assert len(r.devices) == 1
    m = r.devices[0].values
    assert m["cycle_count"] >= 0
    assert m["uptime_seconds"] > 0
    assert m["total_flow_volume_m3"] >= 0
