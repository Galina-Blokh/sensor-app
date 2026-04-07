"""HTTP surface for ``spec_ch3`` stream consumer (``/stream/*``, ``metric_key`` filter)."""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from sensor_app.api.main import create_app
from sensor_app.settings import Settings


def _settings_minimal(repo_root: Path, **overrides: Any) -> Settings:
    cfg: dict[str, Any] = {
        "sensor_db_path": ":memory:",
        "metrics_db_path": ":memory:",
        "schema_path": str(repo_root / "sensor_schema.json"),
        "rate_limit_root": "10000/minute",
        "rate_limit_health": "10000/minute",
        "rate_limit_process": "10000/minute",
        "rate_limit_metrics": "10000/minute",
        "rate_limit_llm_summary": "10000/minute",
        "rate_limit_llm_query": "10000/minute",
        "rate_limit_llm_dq": "10000/minute",
        "rate_limit_stream_status": "10000/minute",
        "rate_limit_stream_flush": "10000/minute",
        "llm_enabled": False,
        "llm_api_key": "",
        "llm_fallback_enabled": False,
        "llm_fallback_base_url": "",
        "llm_fallback_model": "",
        "llm_fallback_api_key": "",
    }
    cfg.update(overrides)
    return Settings(**cfg)


def _valid_queue_event(eid: str) -> dict[str, Any]:
    return {
        "event_id": eid,
        "event_type": "sensor_reading",
        "timestamp": "2024-02-01T12:00:00+00:00",
        "station_id": "S1",
        "device_id": "D1",
        "readings": {
            "discharge_pressure": 7.0,
            "air_flow_rate": 100.0,
            "power_consumption": 50.0,
            "motor_speed": 1500.0,
            "discharge_temp": 40.0,
        },
        "metadata": {},
    }


def test_stream_status_disabled_without_queue(repo_root: Path) -> None:
    app = create_app(_settings_minimal(repo_root, stream_consumer_enabled=True))
    with TestClient(app) as client:
        body = client.get("/stream/status").json()
    assert body["enabled"] is False


def test_stream_flush_503_without_consumer(repo_root: Path) -> None:
    app = create_app(_settings_minimal(repo_root))
    with TestClient(app) as client:
        r = client.post("/stream/flush")
    assert r.status_code == 503


def test_stream_consume_flush_and_metric_key_filter(tmp_path: Path, repo_root: Path) -> None:
    q: queue.Queue[object] = queue.Queue()
    mdb = str(tmp_path / "stream_api_metrics.db")
    settings = _settings_minimal(
        repo_root,
        metrics_db_path=mdb,
        stream_consumer_enabled=True,
        stream_flush_min_events=50_000,
    )
    app = create_app(settings, event_queue=q)
    with TestClient(app) as client:
        assert client.get("/stream/status").json()["enabled"] is True

        q.put(_valid_queue_event("api-e1"))
        q.put(_valid_queue_event("api-e2"))
        q.put({"event_id": "bad", "not": "a sensor event"})

        deadline = time.monotonic() + 5.0
        st: dict[str, Any] = {}
        while time.monotonic() < deadline:
            time.sleep(0.03)
            st = client.get("/stream/status").json()
            if st.get("events_received", 0) >= 3:
                break
        assert st.get("events_received", 0) >= 3
        assert st.get("events_malformed", 0) >= 1
        assert st.get("backlog_depth", -1) == 0

        fr = client.post("/stream/flush")
        assert fr.status_code == 200

        metrics = client.get("/metrics/S1").json()
        assert len(metrics) >= 1
        dev0 = metrics[0]["metrics"]["devices"][0]
        full_keys = set(dev0["metrics"].keys())
        assert "uptime_seconds" in full_keys

        filtered = client.get("/metrics/S1", params={"metric_key": "uptime_seconds"}).json()
        f0 = filtered[0]["metrics"]["devices"][0]["metrics"]
        assert set(f0.keys()) <= {"uptime_seconds"}
        assert "uptime_seconds" in f0


def test_stream_sentinel_triggers_flush(tmp_path: Path, repo_root: Path) -> None:
    q: queue.Queue[object] = queue.Queue()
    mdb = str(tmp_path / "stream_sentinel.db")
    settings = _settings_minimal(
        repo_root,
        metrics_db_path=mdb,
        stream_consumer_enabled=True,
        stream_flush_min_events=50_000,
    )
    app = create_app(settings, event_queue=q)
    with TestClient(app) as client:
        q.put(_valid_queue_event("sent-1"))
        q.put(None)
        deadline = time.monotonic() + 5.0
        flush_count = 0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            st = client.get("/stream/status").json()
            flush_count = st.get("flush_count", 0)
            if flush_count >= 1:
                break
        assert flush_count >= 1
        assert len(client.get("/metrics/S1").json()) >= 1
