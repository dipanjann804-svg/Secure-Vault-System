"""Provider interface.

Adding a second LLM means implementing this interface and registering it in
``llm_service``. The router, the validator and the Flask routes do not change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# Stable error codes the UI and the audit trail can rely on.
ERROR_MISSING_KEY = "missing_api_key"
ERROR_INVALID_KEY = "invalid_api_key"
ERROR_TIMEOUT = "timeout"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_UNAVAILABLE = "provider_unavailable"
ERROR_MODEL_UNAVAILABLE = "model_unavailable"
ERROR_EMPTY = "empty_response"
ERROR_SDK_MISSING = "sdk_missing"
ERROR_GENERIC = "provider_error"


@dataclass(frozen=True)
class LLMResult:
    """One provider call. ``error_detail`` never contains credentials."""

    ok: bool
    text: str = ""
    error_code: str = ""
    error_detail: str = ""
    latency_ms: int = 0


class LLMProvider(ABC):
    """Minimal surface the assistant needs from any language model."""

    name = "base"
    model = ""

    @abstractmethod
    def available(self) -> bool:
        """True when this provider is configured well enough to be called."""

    @abstractmethod
    def generate(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        response_schema: dict | None = None,
        temperature: float = 0.4,
    ) -> LLMResult:
        """Return model text. Implementations must never raise."""
