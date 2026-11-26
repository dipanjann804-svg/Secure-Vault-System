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
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RETRIES = 2

# Codes worth a second attempt. A bad key or an unknown model is not retried.
RETRYABLE = {ERROR_TIMEOUT, ERROR_RATE_LIMITED, ERROR_UNAVAILABLE, ERROR_GENERIC}


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
        sleep=time.sleep,
    ):
        self._api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout = max(1.0, float(timeout or DEFAULT_TIMEOUT))
        self.max_retries = max(0, int(max_retries))
        self._sleep = sleep
        self._client = None

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
        last = LLMResult(False, error_code=ERROR_GENERIC)

        for attempt in range(self.max_retries + 1):
            try:
                from google.genai import types

                config_kwargs = {
                    "system_instruction": system_instruction,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                }
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
                if code not in RETRYABLE or attempt >= self.max_retries:
                    break
                # Small linear backoff; the request is already user-facing.
                self._sleep(0.4 * (attempt + 1))
        return last
