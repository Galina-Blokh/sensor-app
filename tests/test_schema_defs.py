"""Unit: schema JSON loading."""

from __future__ import annotations

from sensor_app.lib.schema_defs import LoadedSchema


def test_schema_loads(schema_path: str) -> None:
    s = LoadedSchema.from_path(schema_path)
    assert "discharge_pressure" in s.sensor_numeric_columns
    assert s.flatline_threshold_minutes["discharge_pressure"] == 30
