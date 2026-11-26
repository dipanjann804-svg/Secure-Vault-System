"""Google Gemini provider built on the official ``google-genai`` SDK.

Safety properties enforced here:

* the API key is read from the environment, used once to build the client, and
  never logged, returned, or embedded in an error message;
* every call has a timeout and a bounded retry budget;
* every exception is mapped to a stable error code so the route layer can fall
  back to deterministic handling instead of failing the request.
"""
from __future__ import annotations

import re
import time

from ai.provider_base import (
    ERROR_EMPTY,
    ERROR_GENERIC,
    ERROR_INVALID_KEY,
    ERROR_MISSING_KEY,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_RATE_LIMITED,
    ERROR_SDK_MISSING,
    ERROR_TIMEOUT,
    ERROR_UNAVAILABLE,
    LLMProvider,
    LLMResult,
)

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 1
# A spoken answer is a couple of sentences. Capping generation keeps the turn
# short instead of letting the model write an essay nobody asked to hear.
DEFAULT_MAX_OUTPUT_TOKENS = 800

# Voice turns are short and user-facing, so the cheapest reasoning setting the
# model supports is requested. On a reasoning model the default ("think as long
# as you like") is the largest single source of latency: two sentences of speech
# can spend several seconds in thinking before the first token is produced.
THINKING_LEVEL = "low"

# Codes worth a second attempt. A bad key or an unknown model is not retried.
RETRYABLE = {ERROR_TIMEOUT, ERROR_RATE_LIMITED, ERROR_UNAVAILABLE, ERROR_GENERIC}


def _thinking_kwargs(types, model: str) -> dict:
    """Fastest supported reasoning setting, feature-detected on the SDK.

    Gemini 3 models take ``thinking_level``; earlier reasoning models take a
    ``thinking_budget`` that can be zeroed. If neither field exists this is a
    no-op and the request is sent unchanged.
    """
    config = getattr(types, "ThinkingConfig", None)
    if config is None:
        return {}
    fields = getattr(config, "model_fields", {})
    if model.lower().startswith("gemini-3") and "thinking_level" in fields:
        return {"thinking_config": config(thinking_level=THINKING_LEVEL)}
    if "thinking_budget" in fields:
        return {"thinking_config": config(thinking_budget=0)}
    return {}


def _thinking_rejected(exc: BaseException) -> bool:
    """True when the API refused the thinking setting itself."""
    text = (str(exc) or "").lower()
    if "thinking" not in text:
        return False
    status = getattr(exc, "code", None)
    return (
        status == 400
        or "invalid" in text
        or "unsupported" in text
        or "not supported" in text
    )


def classify_exception(exc: BaseException) -> str:
    """Map an SDK/transport exception to one of our stable error codes."""
    name = type(exc).__name__.lower()
    text = (str(exc) or "").lower()

    status = getattr(exc, "code", None)
    if isinstance(status, int):
        if status in (401,):
            return ERROR_INVALID_KEY
        if status == 403:
            return ERROR_INVALID_KEY
        if status == 404:
            return ERROR_MODEL_UNAVAILABLE
        if status == 429:
            return ERROR_RATE_LIMITED
        if status == 400 and ("api key" in text or "api_key" in text):
            return ERROR_INVALID_KEY
        if status >= 500:
            return ERROR_UNAVAILABLE

    if "timeout" in name or "timed out" in text or "timeout" in text:
        return ERROR_TIMEOUT
    if "api key not valid" in text or "api_key_invalid" in text or "invalid api key" in text:
        return ERROR_INVALID_KEY
    if "unauthenticated" in text or "permission_denied" in text:
        return ERROR_INVALID_KEY
    if "resource_exhausted" in text or "rate limit" in text or "quota" in text:
        return ERROR_RATE_LIMITED
    if "not found" in text or "unsupported model" in text or "model_not_found" in text:
        return ERROR_MODEL_UNAVAILABLE
    if "connection" in text or "network" in text or "dns" in text or "resolve" in text:
        return ERROR_UNAVAILABLE
    if "import" in name:
        return ERROR_SDK_MISSING
    return ERROR_GENERIC


def _safe_detail(exc: BaseException, api_key: str, limit: int = 200) -> str:
    """A short diagnostic with the credential removed."""
    detail = str(exc) or type(exc).__name__
    if api_key:
        detail = re.sub(re.escape(api_key), "[redacted]", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:limit]


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        sleep=time.sleep,
    ):
        self._api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout = max(1.0, float(timeout or DEFAULT_TIMEOUT))
        self.max_retries = max(0, int(max_retries))
        self.max_output_tokens = max(64, int(max_output_tokens))
        self._sleep = sleep
        self._client = None
        # Cleared for good if the API rejects the thinking setting.
        self._thinking = True

    def available(self) -> bool:
        return bool(self._api_key)

    # -- client ------------------------------------------------------------
    def _build_client(self):
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
        )

    def _get_client(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    # -- generate ----------------------------------------------------------
    def generate(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        response_schema: dict | None = None,
        temperature: float = 0.4,
    ) -> LLMResult:
        if not self.available():
            return LLMResult(False, error_code=ERROR_MISSING_KEY)

        started = time.monotonic()
        attempt = 0

        while True:
            try:
                from google.genai import types

                config_kwargs = {
                    "system_instruction": system_instruction,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                    "max_output_tokens": self.max_output_tokens,
                }
                if self._thinking:
                    config_kwargs.update(_thinking_kwargs(types, self.model))
                # Feature-detect the raw JSON-schema field so the provider still
                # works on older SDK builds.
                if response_schema and "response_json_schema" in getattr(
                    types.GenerateContentConfig, "model_fields", {}
                ):
                    config_kwargs["response_json_schema"] = response_schema
                config = types.GenerateContentConfig(**config_kwargs)

                response = self._get_client().models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=config,
                )
                text = (getattr(response, "text", None) or "").strip()
                elapsed = int((time.monotonic() - started) * 1000)
                if not text:
                    return LLMResult(False, error_code=ERROR_EMPTY, latency_ms=elapsed)
                return LLMResult(True, text=text, latency_ms=elapsed)
            except Exception as exc:  # noqa: BLE001 - every failure becomes a code
                code = classify_exception(exc)
                elapsed = int((time.monotonic() - started) * 1000)
                last = LLMResult(
                    False,
                    error_code=code,
                    error_detail=_safe_detail(exc, self._api_key),
                    latency_ms=elapsed,
                )
                if self._thinking and _thinking_rejected(exc):
                    # The model or key does not accept this reasoning setting.
                    # Retry at once with the plain config rather than failing a
                    # turn the model could have answered.
                    self._thinking = False
                    continue
                if code not in RETRYABLE or attempt >= self.max_retries:
                    return last
                attempt += 1
                # Small linear backoff; the request is already user-facing.
                self._sleep(0.4 * attempt)
