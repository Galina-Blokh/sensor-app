"""LLM layer value types (provider-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Normalized chat completion output."""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
