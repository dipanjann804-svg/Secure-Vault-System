"""The only vocabulary the language model is allowed to use.

Everything the model says is untrusted input. This module defines:

* the request categories the backend accepts,
* the vault tools the model may *propose* (and nothing else),
* the parameter shapes those tools take,
* and the strict validation that turns raw JSON into a ``Decision``.

A rejected payload is never "repaired" into something executable. The caller
falls back to the deterministic planner instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from voice_service import TIER_CONFIRM, TIER_READ, TIER_SENSITIVE

# --- Categories ------------------------------------------------------------
CATEGORY_VAULT_TOOL = "VAULT_TOOL"
CATEGORY_GENERAL = "GENERAL_KNOWLEDGE"
CATEGORY_TECHNICAL = "TECHNICAL_KNOWLEDGE"
CATEGORY_VAULT_HELP = "VAULT_HELP"
CATEGORY_FOLLOW_UP = "FOLLOW_UP"
CATEGORY_CURRENT = "CURRENT_INFORMATION"
CATEGORY_CLARIFICATION = "CLARIFICATION"
CATEGORY_REFUSED = "REFUSED"

CATEGORIES = (
    CATEGORY_VAULT_TOOL,
    CATEGORY_GENERAL,
    CATEGORY_TECHNICAL,
    CATEGORY_VAULT_HELP,
    CATEGORY_FOLLOW_UP,
    CATEGORY_CURRENT,
    CATEGORY_CLARIFICATION,
    CATEGORY_REFUSED,
)

RISK_READ_ONLY = "READ_ONLY"
RISK_MUTATION = "MUTATION"
RISK_FORBIDDEN = "FORBIDDEN"

# Answer sizes. Spoken text stays short so the assistant does not lecture.
MAX_ANSWER_CHARS = 4000
MAX_SPEECH_CHARS = 600
MAX_CLARIFICATION_CHARS = 300
MAX_PARAM_CHARS = 200

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value, limit: int) -> str:
    """Trim, drop control characters and cap length. Never raises."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = _CONTROL_RE.sub(" ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()[:limit]


# --- Tool catalogue --------------------------------------------------------
@dataclass(frozen=True)
class Param:
    """One accepted parameter: the model's name and the handler's plan key."""

    name: str
    plan_key: str
    max_len: int = MAX_PARAM_CHARS
    required: bool = False
    choices: tuple = ()


@dataclass(frozen=True)
class ToolSpec:
    """A vault capability the model may propose.

    ``tier`` comes from this table, never from the model: a model that labels a
    mutation ``READ_ONLY`` cannot make it execute without confirmation.
    """

    name: str
    intent: str
    tier: int
    audit: str
    speech: str
    params: tuple = ()
    action: dict | None = None

    @property
    def is_mutation(self) -> bool:
        return self.tier != TIER_READ

    @property
    def risk(self) -> str:
        return RISK_MUTATION if self.is_mutation else RISK_READ_ONLY

    def param(self, name: str) -> Param | None:
        for item in self.params:
            if item.name == name:
                return item
        return None


TOOLS = {
    "list_files": ToolSpec(
        "list_files", "list_documents", TIER_READ, "voice_list_documents",
        "Here are your files.",
    ),
    "search_files": ToolSpec(
        "search_files", "search_documents", TIER_READ, "voice_search_documents",
        "Searching your files.",
        params=(Param("query", "query", 120, required=True),),
    ),
    "open_file": ToolSpec(
        "open_file", "open_document", TIER_READ, "voice_open_document",
        "Opening that file.",
        params=(Param("query", "document_query"),),
    ),
    "file_details": ToolSpec(
        "file_details", "document_info", TIER_READ, "voice_document_info",
        "Here are the details for that file.",
        params=(Param("query", "document_query"),),
    ),
    "list_folders": ToolSpec(
        "list_folders", "list_folders", TIER_READ, "voice_list_folders",
        "Here are your folders.",
    ),
    "open_folder": ToolSpec(
        "open_folder", "open_folder", TIER_READ, "voice_open_folder",
        "Opening that folder.",
        params=(Param("folder", "folder_query", 50),),
    ),
    "folder_contents": ToolSpec(
        "folder_contents", "list_folder_contents", TIER_READ,
        "voice_list_folder_contents", "Here is what is in that folder.",
        params=(Param("folder", "folder_query", 50),),
    ),
    "list_notes": ToolSpec(
        "list_notes", "list_notes", TIER_READ, "voice_list_notes",
        "Here are your note titles.",
    ),
    "list_contacts": ToolSpec(
        "list_contacts", "list_contacts", TIER_READ, "voice_list_contacts",
        "Here are your saved contacts.",
    ),
    "list_shares": ToolSpec(
        "list_shares", "list_shares", TIER_READ, "voice_list_shares",
        "Here are your active share links.",
    ),
    "vault_summary": ToolSpec(
        "vault_summary", "vault_summary", TIER_READ, "voice_summary",
        "Here is your vault summary.",
    ),
    "open_trash": ToolSpec(
        "open_trash", "open_trash", TIER_READ, "voice_open_trash",
        "Opening the trash.", action={"type": "open_trash"},
    ),
    "open_settings": ToolSpec(
        "open_settings", "open_settings", TIER_READ, "voice_open_settings",
        "Opening settings.", action={"type": "open_settings"},
    ),
    "open_shares": ToolSpec(
        "open_shares", "open_shares", TIER_READ, "voice_open_shares",
        "Opening your share links.", action={"type": "navigate", "url": "/shares"},
    ),
    "open_dashboard": ToolSpec(
        "open_dashboard", "open_dashboard", TIER_READ, "voice_open_dashboard",
        "Opening your dashboard.",
        action={"type": "navigate", "url": "/dashboard"},
    ),
    "create_note": ToolSpec(
        "create_note", "create_note", TIER_CONFIRM, "voice_create_note",
        "I can create that note after you confirm.",
        params=(
            Param("title", "note_title", 120, required=True),
            Param("content", "note_content", 2000, required=True),
        ),
    ),
    "share_file": ToolSpec(
        "share_file", "share_document", TIER_SENSITIVE, "voice_share_prepare",
        "I can prepare that share after you confirm.",
        params=(
            Param("file", "document_query", 200, required=True),
            Param("contact", "contact_query", 150, required=True),
            Param("expiry", "expiry", 8, choices=("10m", "1h", "24h")),
            Param("usage", "usage", 8, choices=("once", "multi")),
        ),
    ),
}

TOOL_NAMES = tuple(TOOLS)


def tool_catalogue_text() -> str:
    """Human-readable tool list for the prompt, generated from this table."""
    lines = []
    for spec in TOOLS.values():
        if spec.params:
            args = ", ".join(
                f'"{p.name}"'
                + (" (required)" if p.required else " (optional)")
                + (f" one of {list(p.choices)}" if p.choices else "")
                for p in spec.params
            )
        else:
            args = "no parameters"
        risk = "requires user confirmation" if spec.is_mutation else "read-only"
        lines.append(f"- {spec.name}: {args}; {risk}")
    return "\n".join(lines)


# --- Decision --------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    """A validated proposal. Never an executed action."""

    category: str
    tool: ToolSpec | None = None
    plan_params: dict = field(default_factory=dict)
    answer: str = ""
    speech: str = ""
    clarification: str = ""
    refusal_reason: str = ""
    risk_level: str = RISK_READ_ONLY
    confidence: float = 0.0
    error_code: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0

    @property
    def tool_name(self) -> str | None:
        return self.tool.name if self.tool else None

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.tool and self.tool.is_mutation)


def validate_parameters(spec: ToolSpec, raw) -> tuple[dict, str, str]:
    """Validate the model's parameters for one tool.

    Returns ``(plan_params, error_code, error_message)``. Unknown keys are a
    hard error rather than being ignored, so an injected ``user_id`` or
    ``owner`` cannot ride along unnoticed.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, "invalid_parameters", "That request had parameters I cannot read."
    allowed = {p.name for p in spec.params}
    for key in raw:
        if not isinstance(key, str) or key not in allowed:
            return {}, "invalid_parameters", "That request used a parameter I do not support."

    plan_params = {}
    for param in spec.params:
        value = raw.get(param.name)
        if value is None:
            value = ""
        if not isinstance(value, str):
            return {}, "invalid_parameters", f"The {param.name} value must be text."
        value = sanitize_text(value, 4000)
        if not value:
            if param.required:
                return {}, "invalid_parameters", f"That request was missing {param.name}."
            continue
        # Over-long values are rejected rather than truncated: silently
        # shortening a search term changes what the user asked for.
        if len(value) > param.max_len:
            return {}, "invalid_parameters", f"The {param.name} value is too long."
        if param.choices and value.lower() not in param.choices:
            return {}, "invalid_parameters", f"The {param.name} value is not one I offer."
        plan_params[param.plan_key] = value.lower() if param.choices else value
    return plan_params, "", ""


def validate_decision(payload) -> tuple[Decision | None, str]:
    """Turn parsed JSON into a ``Decision``, or reject it with an error code."""
    if not isinstance(payload, dict):
        return None, "malformed_json"

    category = str(payload.get("category") or "").strip().upper()
    if category not in CATEGORIES:
        return None, "unknown_category"

    risk = str(payload.get("risk_level") or RISK_READ_ONLY).strip().upper()
    answer = sanitize_text(payload.get("answer"), MAX_ANSWER_CHARS)
    speech = sanitize_text(payload.get("speech"), MAX_SPEECH_CHARS)
    clarification = sanitize_text(
        payload.get("clarification_question"), MAX_CLARIFICATION_CHARS
    )
    refusal = sanitize_text(payload.get("refusal_reason"), MAX_SPEECH_CHARS)
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    # The model may refuse an action, but it may never mark one as safe.
    if risk == RISK_FORBIDDEN:
        return Decision(
            category=CATEGORY_REFUSED,
            refusal_reason=refusal,
            speech=speech,
            risk_level=RISK_FORBIDDEN,
            confidence=confidence,
        ), ""

    if category in (CATEGORY_VAULT_TOOL, CATEGORY_FOLLOW_UP):
        tool_name = str(payload.get("tool_name") or "").strip().lower()
        spec = TOOLS.get(tool_name)
        if spec is None:
            return None, "unknown_tool"
        plan_params, error_code, _message = validate_parameters(
            spec, payload.get("parameters")
        )
        if error_code:
            return None, error_code
        return Decision(
            category=category,
            tool=spec,
            plan_params=plan_params,
            answer=answer,
            speech=speech or spec.speech,
            risk_level=spec.risk,
            confidence=confidence,
        ), ""

    if category == CATEGORY_CLARIFICATION:
        if not clarification:
            return None, "missing_clarification"
        return Decision(
            category=category, clarification=clarification, confidence=confidence
        ), ""

    if category == CATEGORY_REFUSED:
        return Decision(
            category=category,
            refusal_reason=refusal,
            speech=speech,
            risk_level=RISK_FORBIDDEN,
            confidence=confidence,
        ), ""

    if category == CATEGORY_CURRENT:
        # No authorised search provider is configured, so a "current" answer is
        # never trusted, even if the model produced one.
        return Decision(
            category=category, answer=answer, speech=speech, confidence=confidence
        ), ""

    # GENERAL_KNOWLEDGE / TECHNICAL_KNOWLEDGE / VAULT_HELP need real prose.
    if not answer and not speech:
        return None, "empty_answer"
    return Decision(
        category=category,
        answer=answer or speech,
        speech=speech or answer[:MAX_SPEECH_CHARS],
        confidence=confidence,
    ), ""


RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "tool_name": {"type": "string", "nullable": True},
        "parameters": {"type": "object"},
        "answer": {"type": "string", "nullable": True},
        "speech": {"type": "string", "nullable": True},
        "clarification_question": {"type": "string", "nullable": True},
        "refusal_reason": {"type": "string", "nullable": True},
        "risk_level": {
            "type": "string",
            "enum": [RISK_READ_ONLY, RISK_MUTATION, RISK_FORBIDDEN],
        },
        "confidence": {"type": "number"},
    },
    "required": ["category"],
}
