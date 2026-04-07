"""API contract tests (TestClient runs lifespan)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from sensor_app.api.main import create_app
from sensor_app.api.middleware import REQUEST_ID_HEADER
from sensor_app.settings import Settings


def _settings_minimal(repo_root: Path, **overrides: Any) -> Settings:
    """Defaults for API-only tests: in-memory DBs + relaxed rate limits (many requests per test)."""
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
        "llm_enabled": False,
        "llm_api_key": "",
    }
    cfg.update(overrides)
    return Settings(**cfg)


def test_health(repo_root: Path) -> None:
    app = create_app(_settings_minimal(repo_root))
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        body = root.json()
        assert body["service"] == "sensor_app"
        assert body["health"] == "/health"
        assert root.headers.get(REQUEST_ID_HEADER.lower())
        echo = client.get("/", headers={REQUEST_ID_HEADER: "client-trace-1"})
        assert echo.headers.get(REQUEST_ID_HEADER.lower()) == "client-trace-1"
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_process_and_get_metrics(
    tmp_path: Path,
    sensor_db_path: str,
    schema_path: str,
    integration_slice: tuple[str, str, str],
) -> None:
    station_id, start_s, end_s = integration_slice
    metrics_db = tmp_path / "t_metrics.db"
    app = create_app(
        Settings(
            sensor_db_path=sensor_db_path,
            metrics_db_path=str(metrics_db),
            schema_path=schema_path,
            rate_limit_root="10000/minute",
            rate_limit_health="10000/minute",
            rate_limit_process="10000/minute",
            rate_limit_metrics="10000/minute",
            llm_enabled=False,
            llm_api_key="",
        )
    )
    with TestClient(app) as client:
        pr = client.post(
            f"/process/{station_id}",
            params={"start_time": start_s, "end_time": end_s},
        )
        assert pr.status_code == 200, pr.text
        body = pr.json()
        assert body["station_id"] == station_id
        gr = client.get(f"/metrics/{station_id}")
        assert gr.status_code == 200
        rows = gr.json()
        assert len(rows) >= 1
        assert rows[0]["metrics"]["devices"]
        dev0 = rows[0]["metrics"]["devices"][0]["device_id"]
        fr = client.get(f"/metrics/{station_id}", params={"device_id": dev0})
        assert fr.status_code == 200
        filtered = fr.json()
        assert len(filtered) >= 1
        assert all(d["device_id"] == dev0 for d in filtered[0]["metrics"]["devices"])


def test_process_rejects_invalid_datetime(tmp_path: Path, repo_root: Path) -> None:
    app = create_app(
        _settings_minimal(repo_root, metrics_db_path=str(tmp_path / "m.db")),
    )
    with TestClient(app) as client:
        r = client.post(
            "/process/station-x",
            params={"start_time": "not-a-date"},
        )
        assert r.status_code == 400
        assert "invalid datetime" in r.json()["detail"]


def test_process_schema_missing_returns_500(tmp_path: Path, sensor_db_path: str) -> None:
    missing_schema = tmp_path / "does_not_exist.json"
    app = create_app(
        Settings(
            sensor_db_path=sensor_db_path,
            metrics_db_path=str(tmp_path / "m.db"),
            schema_path=str(missing_schema),
            rate_limit_root="10000/minute",
            rate_limit_health="10000/minute",
            rate_limit_process="10000/minute",
            rate_limit_metrics="10000/minute",
            llm_enabled=False,
            llm_api_key="",
        )
    )
    with TestClient(app) as client:
        r = client.post("/process/any-station")
        assert r.status_code == 500
        assert missing_schema.name in r.json()["detail"]


def test_get_metrics_unknown_station_empty(tmp_path: Path, repo_root: Path) -> None:
    app = create_app(
        _settings_minimal(repo_root, metrics_db_path=str(tmp_path / "empty_metrics.db")),
    )
    with TestClient(app) as client:
        r = client.get("/metrics/no-such-station")
        assert r.status_code == 200
        assert r.json() == []


def test_rate_limit_health_returns_429(repo_root: Path) -> None:
    """slowapi enforces per-IP limits; tighten health to prove 429 is returned."""
    app = create_app(_settings_minimal(repo_root, rate_limit_health="2/minute"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        limited = client.get("/health")
        assert limited.status_code == 429
