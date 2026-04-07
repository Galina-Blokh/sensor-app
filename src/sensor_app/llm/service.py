"""Orchestration: prompts + :class:`~sensor_app.llm.client.LLMBackend` + structured NL query execution."""

from __future__ import annotations

import logging
import time
from typing import Any

from sensor_app.lib.metrics_store import StoredSnapshot
from sensor_app.llm.allowed_keys import DEVICE_METRIC_KEYS
from sensor_app.llm.client import LLMBackend, extract_json_object
from sensor_app.llm.context import (
    dumps_bounded,
    multi_snapshot_query_context,
    snapshot_dq_context,
    snapshot_metrics_context,
)
from sensor_app.llm.exceptions import LLMError
from sensor_app.llm.query_execute import (
    format_query_answer,
    parse_and_validate_plan,
    run_query_plan,
)
from sensor_app.llm.types import ChatResult
from sensor_app.settings import Settings

logger = logging.getLogger(__name__)

_SYSTEM_METRICS_SUMMARY = (
    "You are an industrial compressor station analyst. Write a short plain-English health "
    "summary (2–5 sentences) using ONLY the JSON metrics provided. "
    "Do not invent numbers or devices. If data is missing, say so."
)

_SYSTEM_DQ_SUMMARY = (
    "You summarize data-quality findings for operators. Use ONLY the JSON data_quality "
    "object and scores given. Mention missing/flatline/out-of-range style issues if present "
    "in the payload. 2–4 sentences. No fabricated percentages."
)

_SYSTEM_QUERY_PLANNER = (
    "You map user questions to a single structured query over per-device metric values. "
    "Reply with ONE JSON object only, no markdown, keys: "
    "metric_key (string, one of the allowed keys), agg (one of mean|max|min|sum|latest), "
    "device_id (string or null for all devices). "
    "Choose metric_key and agg that best match the question. "
    "If the question cannot be answered from device metrics, use metric_key "
    '"average_pressure_bar" and agg "mean" anyway (caller will show empty result).'
)


class LLMFeatureService:
    """High-level NL features; **store I/O stays in routes** (thread pool)."""

    def __init__(self, backend: LLMBackend, settings: Settings) -> None:
        self._backend = backend
        self._settings = settings

    async def natural_language_metrics_summary(
        self,
        snap: StoredSnapshot,
        *,
        request_id: str | None,
    ) -> tuple[str, str]:
        ctx = snapshot_metrics_context(snap)
        user = dumps_bounded(ctx, self._settings.llm_max_context_chars)
        res = await self._one_shot(
            system=_SYSTEM_METRICS_SUMMARY,
            user=f"Station metrics JSON:\n{user}\n\nSummarize operational health.",
            request_id=request_id,
        )
        return res.text, res.model or self._settings.llm_model

    async def natural_language_dq_summary(
        self,
        snap: StoredSnapshot,
        *,
        request_id: str | None,
    ) -> tuple[str, str]:
        ctx = snapshot_dq_context(snap)
        user = dumps_bounded(ctx, self._settings.llm_max_context_chars)
        res = await self._one_shot(
            system=_SYSTEM_DQ_SUMMARY,
            user=f"Data quality JSON:\n{user}\n\nSummarize data-quality issues.",
            request_id=request_id,
        )
        return res.text, res.model or self._settings.llm_model

    async def natural_language_query(
        self,
        question: str,
        snaps: list[StoredSnapshot],
        *,
        request_id: str | None,
    ) -> tuple[str, dict[str, Any], str]:
        if not snaps:
            facts: dict[str, Any] = {
                "metric_key": "average_pressure_bar",
                "agg": "mean",
                "device_id": None,
                "value": None,
                "n_values": 0,
                "snapshot_ids_used": [],
            }
            return format_query_answer(facts), facts, self._settings.llm_model
        allowed = ", ".join(sorted(DEVICE_METRIC_KEYS))
        aggs = ", ".join(sorted(["mean", "max", "min", "sum", "latest"]))
        payload = multi_snapshot_query_context(snaps)
        bounded = dumps_bounded(payload, self._settings.llm_max_context_chars)
        plan_prompt = (
            f"Allowed metric_key: {allowed}\n"
            f"Allowed agg: {aggs}\n"
            f"Metrics snapshots (newest first):\n{bounded}\n\n"
            f"Question: {question}\n\n"
            "Return one JSON object only."
        )
        res = await self._one_shot(
            system=_SYSTEM_QUERY_PLANNER,
            user=plan_prompt,
            request_id=request_id,
            max_tokens=256,
        )
        try:
            obj = extract_json_object(res.text)
            plan = parse_and_validate_plan(obj)
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"invalid query plan from model: {e}") from e
        facts = run_query_plan(snaps, plan)
        model_used = res.model or self._settings.llm_model
        return format_query_answer(facts), facts, model_used

    async def _one_shot(
        self,
        *,
        system: str,
        user: str,
        request_id: str | None,
        max_tokens: int = 512,
    ) -> ChatResult:
        t0 = time.perf_counter()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            res = await self._backend.chat_completion(
                messages,
                max_completion_tokens=max_tokens,
                request_id=request_id,
            )
        except LLMError as e:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            logger.info(
                "llm_call",
                extra={
                    "outcome": "error",
                    "error": type(e).__name__,
                    "latency_ms": round(dt_ms, 2),
                    "prompt_chars": sum(len(m.get("content", "")) for m in messages),
                    "request_id": request_id or "-",
                },
            )
            raise
        dt_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "llm_call",
            extra={
                "outcome": "ok",
                "latency_ms": round(dt_ms, 2),
                "prompt_tokens": res.prompt_tokens,
                "completion_tokens": res.completion_tokens,
                "prompt_chars": sum(len(m.get("content", "")) for m in messages),
                "model": res.model or "-",
                "request_id": request_id or "-",
            },
        )
        return res
