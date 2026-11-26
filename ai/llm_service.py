"""Configuration and the single entry point the Flask layer calls.

``get_llm_service()`` returns a service that is always safe to call. When no API
key is configured, or the SDK is missing, or ``GEMINI_ENABLED=false``, the
service reports ``available == False`` and the voice route uses the
deterministic planner exactly as before. That is what keeps the assistant
working offline and keeps the test suite network-free.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from ai import prompt_manager, response_parser, schemas
from ai.gemini_provider import DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT, GeminiProvider
from ai.provider_base import ERROR_MISSING_KEY, LLMProvider

METADATA_NAMES = "names"
METADATA_COUNTS = "counts"
METADATA_NONE = "none"


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class AISettings:
    enabled: bool = True
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    metadata: str = METADATA_NAMES
    history_turns: int = 6

    @classmethod
    def from_env(cls) -> "AISettings":
        metadata = (os.environ.get("VOICE_AI_METADATA") or METADATA_NAMES).strip().lower()
        if metadata not in (METADATA_NAMES, METADATA_COUNTS, METADATA_NONE):
            metadata = METADATA_NAMES
        return cls(
            enabled=_env_bool("GEMINI_ENABLED", True),
            api_key=(os.environ.get("GEMINI_API_KEY") or "").strip(),
            model=(os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout=_env_float("GEMINI_TIMEOUT", DEFAULT_TIMEOUT, 3.0, 60.0),
            max_retries=_env_int("GEMINI_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, 4),
            metadata=metadata,
            history_turns=_env_int("VOICE_HISTORY_TURNS", 6, 0, 12),
        )


class LLMService:
    """Provider + prompt + parse + validate. Never raises to the caller."""

    def __init__(self, provider: LLMProvider, settings: AISettings):
        self.provider = provider
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.enabled and self.provider.available())

    @property
    def model(self) -> str:
        return getattr(self.provider, "model", "") or self.settings.model

    def status(self) -> dict:
        """Public, credential-free status for the UI and diagnostics."""
        if not self.settings.enabled:
            state, reason = "disabled", "GEMINI_ENABLED is off"
        elif not self.provider.available():
            state, reason = "unconfigured", "No GEMINI_API_KEY is set"
        else:
            state, reason = "ready", ""
        return {
            "provider": self.provider.name,
            "model": self.model,
            "state": state,
            "reason": reason,
            "metadata": self.settings.metadata,
        }

    def decide(
        self,
        *,
        text: str,
        history_text: str = "",
        knowledge_text: str = "",
        metadata_text: str = "",
        pending_summary: str = "",
        now_text: str = "",
    ) -> schemas.Decision:
        """Ask the model and return a validated decision, or an error decision."""
        if not self.available:
            return schemas.Decision(category="", error_code=ERROR_MISSING_KEY)

        prompt = prompt_manager.build_user_prompt(
            text,
            history_text=history_text,
            knowledge_text=knowledge_text,
            metadata_text=metadata_text,
            pending_summary=pending_summary,
            now_text=now_text,
        )
        result = self.provider.generate(
            system_instruction=prompt_manager.SYSTEM_INSTRUCTION,
            user_prompt=prompt,
            response_schema=schemas.RESPONSE_JSON_SCHEMA,
            temperature=0.4,
        )
        if not result.ok:
            return schemas.Decision(
                category="",
                error_code=result.error_code or "provider_error",
                provider=self.provider.name,
                model=self.model,
                latency_ms=result.latency_ms,
            )

        payload, parse_error = response_parser.parse_decision(result.text)
        if parse_error:
            return schemas.Decision(
                category="",
                error_code=parse_error,
                provider=self.provider.name,
                model=self.model,
                latency_ms=result.latency_ms,
            )

        decision, validation_error = schemas.validate_decision(payload)
        if validation_error:
            return schemas.Decision(
                category="",
                error_code=validation_error,
                provider=self.provider.name,
                model=self.model,
                latency_ms=result.latency_ms,
            )

        return replace(
            decision,
            provider=self.provider.name,
            model=self.model,
            latency_ms=result.latency_ms,
        )


_service_cache: dict = {"signature": None, "service": None}


def _signature(settings: AISettings) -> tuple:
    return (
        settings.enabled,
        settings.api_key,
        settings.model,
        settings.timeout,
        settings.max_retries,
        settings.metadata,
        settings.history_turns,
    )


def get_llm_service() -> LLMService:
    """Current service, rebuilt only when the environment changes."""
    settings = AISettings.from_env()
    signature = _signature(settings)
    if _service_cache["signature"] == signature and _service_cache["service"] is not None:
        return _service_cache["service"]
    provider = GeminiProvider(
        api_key=settings.api_key,
        model=settings.model,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )
    service = LLMService(provider, settings)
    _service_cache["signature"] = signature
    _service_cache["service"] = service
    return service


def reset_llm_service() -> None:
    """Drop the cached service (tests, or after rotating the key)."""
    _service_cache["signature"] = None
    _service_cache["service"] = None
