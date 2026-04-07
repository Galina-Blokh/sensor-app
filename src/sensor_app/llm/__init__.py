"""LLM integration: OpenAI-compatible chat (OpenAI, Groq, self-hosted, …), optional fallback chain."""

from sensor_app.llm.client import (
    LLMBackend,
    OpenAICompatibleChatBackend,
    PrimaryWithFallbackBackend,
)
from sensor_app.llm.service import LLMFeatureService

__all__ = [
    "LLMBackend",
    "LLMFeatureService",
    "OpenAICompatibleChatBackend",
    "PrimaryWithFallbackBackend",
]
