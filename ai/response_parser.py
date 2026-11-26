"""JSON extraction for model output.

The model is asked for JSON, but output is still treated as hostile text. A
response that cannot be parsed is rejected with an error code; it is never
patched into something that looks executable.
"""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def strip_fences(text: str) -> str:
    text = (text or "").strip()
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


def parse_decision(raw_text: str) -> tuple[dict | None, str]:
    """Return ``(payload, error_code)``. Exactly one is meaningful."""
    text = strip_fences(raw_text)
    if not text:
        return None, "empty_response"
    # Some models prepend a sentence before the object; take the outermost
    # braces rather than guessing at the content.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None, "malformed_json"
        text = text[start:end + 1]
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None, "malformed_json"
    if not isinstance(payload, dict):
        return None, "malformed_json"
    return payload, ""
