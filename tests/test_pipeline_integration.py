"""Integration: full pipeline against real SQLite + schema files."""

from __future__ import annotations

from datetime import datetime

from sensor_app.lib.pipeline import MissingDataStrategy, PipelineConfig, run_station_pipeline


def test_pipeline_real_db(
    sensor_db_path: str,
    schema_path: str,
    integration_slice: tuple[str, str, str],
) -> None:
    station_id, start_s, end_s = integration_slice
    start = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
    cfg = PipelineConfig(
        resample_rule="1h",
        missing_strategy=MissingDataStrategy.INTERPOLATE,
    )
    out = run_station_pipeline(
        sensor_db_path,
        schema_path,
        station_id,
        start,
        end,
        cfg,
    )
    assert out["station_id"] == station_id
    assert "data_quality" in out
    assert "metrics" in out
    assert "devices" in out["metrics"]
