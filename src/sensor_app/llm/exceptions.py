"""Errors raised by the LLM transport layer (mapped to HTTP in the API)."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM client failures."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMTimeoutError(LLMError):
    """HTTP timeout waiting for the provider."""

    def __init__(self, message: str = "LLM request timed out") -> None:
        super().__init__(message, retryable=True)


class LLMUpstreamError(LLMError):
    """Non-success HTTP status or malformed provider payload."""

    def __init__(
        self, message: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.status_code = status_code
