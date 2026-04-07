"""LLM routes and transport: mocked backend; no provider credentials required."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient

from sensor_app.api.main import create_app
from sensor_app.lib.metrics_store import MetricsStore
from sensor_app.llm.client import OpenAICompatibleChatBackend
from sensor_app.llm.types import ChatResult
from sensor_app.settings import Settings


def _settings_with_llm_limits(repo_root: Path, **overrides: Any) -> Settings:
    """Defaults isolate tests from a developer ``.env`` (LLM env vars would change behavior)."""
    base: dict[str, Any] = {
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
        "llm_fallback_enabled": False,
        "llm_fallback_base_url": "",
        "llm_fallback_model": "",
        "llm_fallback_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)


class ScriptedLLM:
    """Deterministic async backend for wiring tests (no HTTP)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, str]]] = []

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        request_id: str | None = None,
    ) -> ChatResult:
        _ = max_completion_tokens
        _ = request_id
        self.calls.append(messages)
        idx = min(self._i, len(self._responses) - 1)
        text = self._responses[idx]
        self._i += 1
        return ChatResult(text=text)


def _seed_snapshot(db_path: str) -> int:
    store = MetricsStore(db_path)
    store.init()
    payload: dict[str, Any] = {
        "station_id": "S1",
        "window_start": "2020-01-01T00:00:00+00:00",
        "window_end": "2020-01-02T00:00:00+00:00",
        "data_quality": {"missing": {"share": 0.02}, "flatline": {"share": 0.01}},
        "metrics": {
            "devices": [
                {"device_id": "D1", "values": {"average_pressure_bar": 7.5, "uptime_seconds": 100}},
                {"device_id": "D2", "values": {"average_pressure_bar": 8.5, "uptime_seconds": 200}},
            ],
            "extras": {},
        },
    }
    return store.save_snapshot(payload, "2020-01-01T00:00:01+00:00")


def test_llm_routes_return_503_when_backend_missing(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "m.db")
    _seed_snapshot(mdb)
    app = create_app(_settings_with_llm_limits(repo_root, metrics_db_path=mdb))
    with TestClient(app) as client:
        r = client.post("/llm/metrics-summary", json={"station_id": "S1"})
        assert r.status_code == 503
        assert (
            "disabled" in r.json()["detail"].lower()
            or "not configured" in r.json()["detail"].lower()
        )


def test_llm_metrics_summary_uses_mock_backend(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "m.db")
    _seed_snapshot(mdb)
    llm = ScriptedLLM(["Compressors look stable."])
    app = create_app(
        _settings_with_llm_limits(repo_root, metrics_db_path=mdb),
        llm_backend_override=llm,
    )
    with TestClient(app) as client:
        r = client.post("/llm/metrics-summary", json={"station_id": "S1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] == "Compressors look stable."
        assert body["snapshot_id"] >= 1
        assert len(llm.calls) == 1
        roles = {m["role"] for m in llm.calls[0]}
        assert "system" in roles and "user" in roles


def test_llm_data_quality_summary_uses_mock_backend(tmp_path: Path, repo_root: Path) -> None:
    """Third spec_ch2 feature: DQ NL summary wired; mock output is deterministic (not provider prose)."""
    mdb = str(tmp_path / "m.db")
    _seed_snapshot(mdb)
    llm = ScriptedLLM(["Missing and flatline shares are low."])
    app = create_app(
        _settings_with_llm_limits(repo_root, metrics_db_path=mdb),
        llm_backend_override=llm,
    )
    with TestClient(app) as client:
        r = client.post("/llm/data-quality-summary", json={"station_id": "S1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] == "Missing and flatline shares are low."
        assert body["snapshot_id"] >= 1
        assert len(llm.calls) == 1
        user_msg = next(m["content"] for m in llm.calls[0] if m["role"] == "user")
        assert "data_quality" in user_msg or "missing" in user_msg.lower()


def test_llm_query_parses_plan_and_aggregates_in_python(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "m.db")
    _seed_snapshot(mdb)
    plan = '{"metric_key":"average_pressure_bar","agg":"mean","device_id":null}'
    llm = ScriptedLLM([plan])
    app = create_app(
        _settings_with_llm_limits(repo_root, metrics_db_path=mdb),
        llm_backend_override=llm,
    )
    with TestClient(app) as client:
        r = client.post(
            "/llm/query",
            json={"station_id": "S1", "question": "What is mean pressure?"},
        )
        assert r.status_code == 200, r.text
        facts = r.json()["facts"]
        assert facts["metric_key"] == "average_pressure_bar"
        assert facts["agg"] == "mean"
        assert facts["n_values"] == 2
        assert abs(float(facts["value"]) - 8.0) < 1e-9
        assert "8" in r.json()["answer"]


def test_llm_query_invalid_plan_returns_502(tmp_path: Path, repo_root: Path) -> None:
    mdb = str(tmp_path / "m.db")
    _seed_snapshot(mdb)
    llm = ScriptedLLM(["not-json"])
    app = create_app(
        _settings_with_llm_limits(repo_root, metrics_db_path=mdb),
        llm_backend_override=llm,
    )
    with TestClient(app) as client:
        r = client.post("/llm/query", json={"station_id": "S1", "question": "?"})
        assert r.status_code == 502


def test_openai_compatible_backend_retries_on_429() -> None:
    n = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["hits"] += 1
        if n["hits"] < 3:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            backend = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://llm.test/v1",
                api_key="secret",
                model="m",
                max_retries=4,
                backoff_base_ms=1,
            )
            res = await backend.chat_completion(
                [{"role": "user", "content": "hi"}],
                request_id="t1",
            )
        assert res.text == "done"
        assert res.prompt_tokens == 1
        assert res.model == "m"
        assert n["hits"] == 3

    asyncio.run(_run())


def test_metrics_store_get_snapshot_by_id_scoped(tmp_path: Path) -> None:
    mdb = str(tmp_path / "scoped.db")
    sid = _seed_snapshot(mdb)
    store = MetricsStore(mdb)
    assert store.get_snapshot_by_id("S1", sid) is not None
    assert store.get_snapshot_by_id("OTHER", sid) is None
