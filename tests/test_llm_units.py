"""Unit tests for ``sensor_app.llm`` helpers (higher coverage; no FastAPI lifespan)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from sensor_app.lib.metrics_store import StoredSnapshot
from sensor_app.llm import service as llm_service_module
from sensor_app.llm.client import OpenAICompatibleChatBackend, _parse_chat_json, extract_json_object
from sensor_app.llm.context import dumps_bounded, snapshot_metrics_context
from sensor_app.llm.exceptions import LLMTimeoutError, LLMUpstreamError
from sensor_app.llm.query_execute import (
    aggregate_values,
    collect_numeric_series,
    format_query_answer,
    parse_and_validate_plan,
    run_query_plan,
)
from sensor_app.llm.service import LLMFeatureService
from sensor_app.llm.types import ChatResult
from sensor_app.settings import Settings


def _snap(
    *,
    snap_id: int = 1,
    metrics: dict[str, Any] | None = None,
    dq: dict[str, Any] | None = None,
) -> StoredSnapshot:
    return StoredSnapshot(
        id=snap_id,
        station_id="S",
        window_start="",
        window_end="",
        computed_at="t",
        data_quality=dq or {},
        metrics=metrics or {"devices": []},
    )


def test_parse_and_validate_plan_errors() -> None:
    with pytest.raises(ValueError, match="metric_key"):
        parse_and_validate_plan({"metric_key": "nope", "agg": "mean"})
    with pytest.raises(ValueError, match="agg"):
        parse_and_validate_plan({"metric_key": "average_pressure_bar", "agg": "median"})
    with pytest.raises(ValueError, match="device_id"):
        parse_and_validate_plan(
            {"metric_key": "average_pressure_bar", "agg": "mean", "device_id": 1}
        )


def test_parse_and_validate_plan_empty_device_id_becomes_none() -> None:
    p = parse_and_validate_plan(
        {"metric_key": "average_pressure_bar", "agg": "sum", "device_id": ""}
    )
    assert p.device_id is None


def test_collect_numeric_series_skips_bad_shapes() -> None:
    s = _snap(
        metrics={
            "devices": [
                "not-a-dict",
                {"device_id": "A", "values": {"average_pressure_bar": 1.0}},
                {"device_id": "B", "values": "bad"},
                {"device_id": "C", "values": {"average_pressure_bar": True}},
                {"device_id": "D", "values": {"average_pressure_bar": float("nan")}},
            ]
        }
    )
    assert collect_numeric_series([s], "average_pressure_bar", None) == [1.0]
    assert collect_numeric_series([s], "average_pressure_bar", "B") == []
    assert collect_numeric_series([s], "average_pressure_bar", "A") == [1.0]


def test_collect_numeric_series_devices_not_list() -> None:
    s = _snap(metrics={"devices": {}})
    assert collect_numeric_series([s], "average_pressure_bar", None) == []


def test_aggregate_values_edge_cases() -> None:
    assert aggregate_values([], "mean") is None
    assert aggregate_values([1.0, 2.0], "not-an-agg") is None


def test_run_query_plan_latest_branches() -> None:
    p = parse_and_validate_plan(
        {"metric_key": "average_pressure_bar", "agg": "latest", "device_id": None}
    )
    empty = run_query_plan([], p)
    assert empty["value"] is None and empty["n_values"] == 0

    s = _snap(
        metrics={
            "devices": [
                {"device_id": "a", "values": {"average_pressure_bar": 2.0}},
                {"device_id": "b", "values": {"average_pressure_bar": 4.0}},
            ]
        }
    )
    multi = run_query_plan([s], p)
    assert multi["value"] == 3.0 and multi["n_values"] == 2

    s2 = _snap(
        snap_id=2,
        metrics={"devices": [{"device_id": "x", "values": {"average_pressure_bar": 9.0}}]},
    )
    none_series = run_query_plan(
        [s2],
        parse_and_validate_plan(
            {
                "metric_key": "average_pressure_bar",
                "agg": "latest",
                "device_id": "missing",
            }
        ),
    )
    assert none_series["value"] is None and none_series["n_values"] == 0


def test_format_query_answer_branches() -> None:
    assert "No numeric" in format_query_answer(
        {"value": None, "metric_key": "average_pressure_bar", "agg": "mean", "n_values": 0}
    )
    s = format_query_answer(
        {
            "value": 12345.678,
            "metric_key": "average_pressure_bar",
            "agg": "mean",
            "n_values": 1,
            "device_id": None,
        }
    )
    assert "12346" in s or "12345" in s
    s2 = format_query_answer(
        {
            "value": 0.00001,
            "metric_key": "average_pressure_bar",
            "agg": "mean",
            "n_values": 1,
        }
    )
    assert "1e-05" in s2 or "0.0000" in s2
    s3 = format_query_answer(
        {
            "value": "not-float",
            "metric_key": "average_pressure_bar",
            "agg": "mean",
            "n_values": 1,
            "device_id": "D1",
        }
    )
    assert "D1" in s3 and "not-float" in s3


def test_dumps_bounded_truncates() -> None:
    long_obj = {"x": "a" * 100}
    out = dumps_bounded(long_obj, max_chars=20)
    assert len(out) == 20
    assert dumps_bounded({"k": 1}, max_chars=1000).startswith('{"k":1}')


def test_snapshot_metrics_context_skips_non_dict_device() -> None:
    s = _snap(metrics={"devices": [{"device_id": 1, "values": {7: 8}}, "x"]})
    ctx = snapshot_metrics_context(s)
    assert ctx["devices"] == [{"device_id": 1, "values": {7: 8}}]


def test_extract_json_object_fenced_and_errors() -> None:
    assert extract_json_object('```json\n{"a":1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("no braces here")
    with pytest.raises(ValueError):
        extract_json_object("[1,2]")


def test_parse_chat_json_and_errors() -> None:
    r = _parse_chat_json(
        {
            "choices": [{"message": {"content": " hi "}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 7},
        }
    )
    assert r.text == "hi" and r.prompt_tokens == 3 and r.completion_tokens == 7
    r2 = _parse_chat_json({"choices": [{"message": {"content": [1, 2]}}]})
    assert r2.text == "[1, 2]"
    with pytest.raises(LLMUpstreamError):
        _parse_chat_json({})


def test_openai_backend_client_error_no_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            b = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://t/v1",
                api_key="k",
                model="m",
                max_retries=0,
                backoff_base_ms=1,
            )
            with pytest.raises(LLMUpstreamError) as ei:
                await b.chat_completion([{"role": "user", "content": "x"}])
            assert ei.value.status_code == 401

    asyncio.run(_run())


def test_openai_backend_500_exhausts_retries() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(500, json={"error": "srv"})

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            b = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://t/v1",
                api_key="k",
                model="m",
                max_retries=2,
                backoff_base_ms=1,
            )
            with pytest.raises(LLMUpstreamError):
                await b.chat_completion([{"role": "user", "content": "x"}])

    asyncio.run(_run())
    assert n["i"] == 3


def test_openai_backend_timeout_retries_then_raises() -> None:
    n = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["i"] += 1
        raise httpx.ReadTimeout("boom")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            b = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://t/v1",
                api_key="k",
                model="m",
                max_retries=2,
                backoff_base_ms=1,
            )
            with pytest.raises(LLMTimeoutError):
                await b.chat_completion([{"role": "user", "content": "x"}])

    asyncio.run(_run())
    assert n["i"] == 3


def test_openai_backend_http_error_wraps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            b = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://t/v1",
                api_key="k",
                model="m",
                max_retries=0,
                backoff_base_ms=1,
            )
            with pytest.raises(LLMUpstreamError, match="transport"):
                await b.chat_completion([{"role": "user", "content": "x"}])

    asyncio.run(_run())


def test_openai_backend_sends_request_id_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["hdr"] = request.headers.get("X-Request-ID", "")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            b = OpenAICompatibleChatBackend(
                client=client,
                base_url="http://t/v1",
                api_key="k",
                model="m",
                max_retries=0,
                backoff_base_ms=1,
            )
            await b.chat_completion([], request_id="trace-99")

    asyncio.run(_run())
    assert seen["hdr"] == "trace-99"


def test_llm_feature_service_empty_snaps_skips_backend() -> None:
    called: list[int] = []

    class T:
        async def chat_completion(self, *a: Any, **k: Any) -> ChatResult:
            called.append(1)
            return ChatResult(text="should-not-run")

    svc = LLMFeatureService(
        T(),
        Settings(
            sensor_db_path=":memory:",
            metrics_db_path=":memory:",
            schema_path="sensor_schema.json",
            llm_enabled=False,
            llm_api_key="",
        ),
    )

    async def _run() -> None:
        ans, facts = await svc.natural_language_query("q?", [], request_id=None)
        assert not called
        assert facts["n_values"] == 0
        assert "No numeric" in ans

    asyncio.run(_run())


def test_llm_feature_service_one_shot_logs_on_llm_error() -> None:
    """Error path logs once with ``outcome=error`` (``caplog`` misses child loggers that propagate)."""

    class Err:
        async def chat_completion(self, *a: Any, **k: Any) -> ChatResult:
            raise LLMTimeoutError("nope")

    svc = LLMFeatureService(
        Err(),
        Settings(
            sensor_db_path=":memory:",
            metrics_db_path=":memory:",
            schema_path="sensor_schema.json",
            llm_enabled=False,
            llm_api_key="",
        ),
    )
    snap = _snap(metrics={"devices": []})

    async def _run() -> None:
        with pytest.raises(LLMTimeoutError):
            await svc.natural_language_metrics_summary(snap, request_id="rid")

    with patch.object(llm_service_module.logger, "info") as mock_info:
        asyncio.run(_run())

    mock_info.assert_called_once()
    assert mock_info.call_args[0][0] == "llm_call"
    extra = mock_info.call_args.kwargs["extra"]
    assert extra["outcome"] == "error"
    assert extra["error"] == "LLMTimeoutError"
    assert extra["request_id"] == "rid"
