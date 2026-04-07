"""LLM integration layer (swappable backend, structured metric access for NL query)."""

from sensor_app.llm.client import LLMBackend, OpenAICompatibleChatBackend
from sensor_app.llm.service import LLMFeatureService

__all__ = ["LLMBackend", "LLMFeatureService", "OpenAICompatibleChatBackend"]
