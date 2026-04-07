"""spec_ch3 stream: event parsing and processor → ``metric_snapshots``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sensor_app.lib.metrics_store import MetricsStore
from sensor_app.lib.pipeline import PipelineConfig
from sensor_app.lib.schema_defs import LoadedSchema
from sensor_app.lib.sensor_event import READING_NUMERIC_KEYS, parse_sensor_reading_event
from sensor_app.lib.stream_consumer import StreamProcessor


def _valid_event(eid: str, station: str = "S1", device: str = "D1") -> dict[str, Any]:
    return {
        "event_id": eid,
        "event_type": "sensor_reading",
        "timestamp": "2024-02-01T12:00:00+00:00",
        "station_id": station,
        "device_id": device,
        "readings": {
            "discharge_pressure": 7.0,
            "air_flow_rate": 100.0,
            "power_consumption": 50.0,
            "motor_speed": 1500.0,
            "discharge_temp": 40.0,
        },
        "metadata": {},
    }


def test_parse_sensor_event_accepts_producer_shape() -> None:
    row = parse_sensor_reading_event(_valid_event("x"))
    assert row is not None
    assert row["station_id"] == "S1"
    assert row["discharge_pressure"] == 7.0


def test_parse_sensor_event_rejects_malformed() -> None:
    assert parse_sensor_reading_event({}) is None
    assert parse_sensor_reading_event({"garbage": True}) is None
    assert (
        parse_sensor_reading_event(
            {
                "event_id": "a",
                "event_type": "sensor_reading",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "station_id": "S",
                "device_id": "D",
                "readings": {"discharge_pressure": "nope"},
            }
        )
        is None
    )


def test_stream_processor_flush_writes_snapshot(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "stream_metrics.db")
    store = MetricsStore(mdb)
    store.init()
    schema = LoadedSchema.from_path(str(repo_root / "sensor_schema.json"))
    proc = StreamProcessor(
        store=store,
        schema=schema,
        pipeline_config=PipelineConfig(),
        flush_min_events=10_000,
        max_seen_event_ids=500,
    )
    proc.handle(_valid_event("e1", device="dev_a"))
    proc.handle(_valid_event("e2", device="dev_a", station="S1"))
    assert store.list_snapshots("S1") == []
    proc.flush()
    snaps = store.list_snapshots("S1")
    assert len(snaps) == 1
    assert snaps[0].data_quality["extra"].get("ingest") == "stream_consumer"


def test_stream_processor_idempotent_event_id(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "m2.db")
    store = MetricsStore(mdb)
    store.init()
    schema = LoadedSchema.from_path(str(repo_root / "sensor_schema.json"))
    proc = StreamProcessor(
        store=store,
        schema=schema,
        pipeline_config=PipelineConfig(),
        flush_min_events=5,
        max_seen_event_ids=500,
    )
    ev = _valid_event("same-id")
    proc.handle(ev)
    proc.handle(ev)
    snap = proc.status_snapshot(None)
    assert snap["events_duplicate"] == 1
    proc.flush()
    rows = store.list_snapshots("S1")
    assert len(rows) == 1
    # Single physical row after dedupe
    assert rows[0].data_quality["rows_before_clean"] == 1


@pytest.mark.parametrize("key", list(READING_NUMERIC_KEYS))
def test_parse_accepts_none_reading(key: str) -> None:
    r: dict[str, float | None] = dict.fromkeys(READING_NUMERIC_KEYS, 1.0)
    r[key] = None
    d = _valid_event("z")
    d["readings"] = r
    assert parse_sensor_reading_event(d) is not None
