"""Short-lived, per-user conversation memory.

Storage is the existing ``VoiceContext.context_json`` column, so no schema
migration is needed. Only a handful of recent turns are kept, they expire with
the same window as the document-reference context, and they are scoped to the
signed-in owner by the row itself.

What is never kept: audio, note bodies, passwords, keys, email addresses, and
anything after a dictated "content" marker. Dictated note text is redacted
before it reaches this module.
"""
from __future__ import annotations

import re
import time

MAX_TURNS = 6
MAX_USER_CHARS = 240
MAX_ASSISTANT_CHARS = 320

_REDACT_RE = re.compile(
    r"\s+(?:with\s+(?:the\s+)?content|saying|that\s+says|containing|content)\s*:?\s*.*$",
    re.I | re.S,
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_ORDINALS = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
    "last": -1, "final": -1,
}


def redact(text: str) -> str:
    """Remove dictated secrets before anything is remembered."""
    text = _EMAIL_RE.sub("[email]", text or "")
    return _REDACT_RE.sub("", text).strip()


def _now() -> float:
    return time.time()


def read_turns(data: dict, ttl_seconds: int, now: float | None = None) -> list[dict]:
    """Live turns for this user, dropping anything past the TTL."""
    if not isinstance(data, dict):
        return []
    turns = data.get("turns")
    if not isinstance(turns, list):
        return []
    now = _now() if now is None else now
    live = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = turn.get("text")
        stamp = turn.get("ts")
        if role not in ("user", "assistant") or not isinstance(text, str):
            continue
        try:
            stamp = float(stamp)
        except (TypeError, ValueError):
            continue
        if now - stamp > ttl_seconds:
            continue
        live.append({"role": role, "text": text, "ts": stamp})
    return live[-MAX_TURNS:]


def append_turn(
    data: dict,
    role: str,
    text: str,
    ttl_seconds: int,
    now: float | None = None,
) -> None:
    """Append one turn to the in-memory context dict (caller commits)."""
    if role not in ("user", "assistant"):
        return
    text = redact(text or "")
    limit = MAX_USER_CHARS if role == "user" else MAX_ASSISTANT_CHARS
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    if not text:
        return
    now = _now() if now is None else now
    turns = read_turns(data, ttl_seconds, now)
    turns.append({"role": role, "text": text, "ts": now})
    data["turns"] = turns[-MAX_TURNS:]


def format_history(turns: list[dict], limit: int = MAX_TURNS) -> str:
    """Plain-text transcript for the prompt."""
    lines = []
    for turn in turns[-limit:]:
        speaker = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{speaker}: {turn['text']}")
    return "\n".join(lines)


def last_user_question(turns: list[dict], skip_latest: bool = True) -> str:
    """The most recent earlier question, for "what did I ask before?"."""
    questions = [t["text"] for t in turns if t["role"] == "user"]
    if skip_latest and questions:
        questions = questions[:-1]
    return questions[-1] if questions else ""


def remember_documents(data: dict, doc_ids, labels=None) -> None:
    """Keep the last result set so "open the second one" can resolve."""
    data["last_document_ids"] = [int(value) for value in (doc_ids or [])][:10]
    if labels is not None:
        data["last_document_labels"] = [str(label)[:120] for label in labels][:10]


def remember_intent(data: dict, intent: str) -> None:
    if intent:
        data["last_intent"] = str(intent)[:60]


def document_ids(data: dict) -> list[int]:
    raw = (data or {}).get("last_document_ids") or []
    ids = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def resolve_ordinal(text: str) -> int | None:
    """1-based position in the last result set, or -1 for "last"."""
    lowered = (text or "").lower()
    match = re.search(
        r"\b(first|second|third|fourth|fifth|last|final|1st|2nd|3rd|4th|5th)\b",
        lowered,
    )
    if match:
        return _ORDINALS.get(match.group(1))
    if re.search(r"\b(number\s+)?(one|two|three|four|five)\b", lowered) and re.search(
        r"\b(open|show|the)\b", lowered
    ):
        for word, index in (("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5)):
            if re.search(rf"\b{word}\b", lowered):
                return index
    return None
