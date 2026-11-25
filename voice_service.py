"""Safe, deterministic command planning for the tap-to-talk assistant.

The browser owns speech-to-text and text-to-speech. This module receives only
the recognized English text and turns it into a small plan. It never calls an
external model and it never accepts an email address, a password, or a
destructive instruction from the microphone.

The planner is a tool registry, not a pile of per-phrase branches:

  * A ``Tool`` is one capability (``search_documents``, ``list_folders`` ...)
    plus every phrasing that reaches it. Adding a way to ask is a new pattern
    on an existing tool; adding a capability is one ``Tool`` here and one
    handler in ``voice.py``.
  * Every tool carries its own safety tier, so the route layer never has to
    guess how risky a match is.
  * Patterns are matched in registry order. Navigation runs before the refusal
    guard (so "show the trash" stays available), and the guard runs before any
    lookup tool, so a blocked action can never be routed into a read.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

VOICE_LANGUAGE = "en-US"
VOICE_MAX_TRANSCRIPT = 600
VOICE_MAX_NOTE_TITLE = 120
VOICE_MAX_NOTE_CONTENT = 2000
VOICE_PENDING_TTL_SECONDS = max(60, int(os.environ.get("VOICE_PENDING_TTL_SECONDS", "300")))
VOICE_CONTEXT_TTL_SECONDS = max(300, int(os.environ.get("VOICE_CONTEXT_TTL_SECONDS", "1800")))
VOICE_RATE_WINDOW_SECONDS = max(30, int(os.environ.get("VOICE_RATE_WINDOW_SECONDS", "60")))
VOICE_RATE_LIMIT = max(3, int(os.environ.get("VOICE_RATE_LIMIT", "20")))

TIER_READ = 0
TIER_CONFIRM = 1
TIER_SENSITIVE = 2
TIER_BLOCKED = 3

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_EXPIRY_LABELS = {"10m": "10 minutes", "1h": "1 hour", "24h": "24 hours"}
_USAGE_LABELS = {"once": "one-time", "multi": "multiple-use"}

# People rarely dictate a bare command. Strip the polite padding around it so
# "can you please open my passport" reaches the same planner as "open passport".
_LEAD_IN_PATTERNS = (
    r"^(?:hey|hi|hello|ok|okay|so|um|uh|er|now|alright|buffy)\b[\s,]*",
    r"^please\b[\s,]*",
    r"^(?:can|could|would|will)\s+you(?:\s+please)?\b[\s,]*",
    r"^i\s*(?:'d|would)\s+like\s+(?:you\s+)?to\b[\s,]*",
    r"^i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\b[\s,]*",
)
_TRAILING_NOISE_RE = re.compile(
    r"(?:[\s,.!?]+(?:please|now|right now|do it|go ahead|thanks|thank you|"
    r"for me|ok|okay))+[\s.!?]*$",
    re.I,
)
_FILE_PREFIX_RE = re.compile(
    r"^(?:file|document|doc|pdf|image)\s+(?:named|called|titled|name)\s+", re.I
)
_ARTICLE_RE = re.compile(r"^(?:my|the|a|an|this|that)\s+", re.I)
_NOTE_BODY_RE = re.compile(
    r"\b(?:with\s+(?:the\s+)?content|saying|that\s+says|containing|content)\b", re.I
)

_COUNT_RE = re.compile(r"^how\s+many\b", re.I)

# A question asks for information, so it is answered instead of refused. Only
# an imperative can be a blocked action.
_QUESTION_START_RE = re.compile(
    r"^(?:how|what|when|where|which|who|why|is|are|was|were|do|does|did|"
    r"can|could|would|should|may|might)\b",
    re.I,
)


def normalize_transcript(text: str) -> str:
    """Trim, collapse whitespace, and cap the text before parsing."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:VOICE_MAX_TRANSCRIPT]


def _clean_slot(value: str) -> str:
    return (value or "").strip(" \t\r\n.,!?;:\"'")


def _is_context_reference(value: str) -> bool:
    value = _clean_slot(value).lower()
    value = re.sub(r"^(?:the|that|this|my|a|an|some)\s+", "", value)
    value = re.sub(r"^(?:last|latest|most recent|previous)\s+", "", value)
    return value in {"", "it", "that", "this", "file", "document", "one"}


def _clean_query(value: str) -> str:
    """Trim a spoken name: punctuation, trailing "please/do it", articles."""
    value = _clean_slot(value)
    value = _TRAILING_NOISE_RE.sub("", value)
    value = _clean_slot(value)
    return value


def _folder_slot(value: str) -> str:
    """A folder name from speech; "my" or "the" means the user named none."""
    value = _clean_query(value)
    if value.lower() in {"", "my", "the", "a", "an", "this", "that", "some"}:
        return ""
    return "" if _is_context_reference(value) else value


def _document_slot(value: str) -> str:
    """A filename from speech, with "file named ..." padding removed."""
    query = _clean_query(value)
    if _is_context_reference(query):
        return ""
    query = _ARTICLE_RE.sub("", query)
    query = _FILE_PREFIX_RE.sub("", query)
    query = re.sub(r"^(?:named|called|titled)\s+", "", query, flags=re.I)
    query = re.sub(r"\s+(?:file|document|doc)$", "", query, flags=re.I)
    return _clean_slot(query)


def _info_slot(value: str) -> str:
    """A filename from a "where/when/how big is ..." question."""
    query = _clean_query(value)
    query = _ARTICLE_RE.sub("", query)
    query = re.sub(r"^(?:file|document|doc)\s+", "", query, flags=re.I)
    query = _FILE_PREFIX_RE.sub("", query)
    query = re.sub(r"\s+(?:file|document|doc)$", "", query, flags=re.I)
    return _clean_slot(query)


def _search_slot(value: str) -> str:
    """The words to look for in a search request."""
    query = _clean_query(value)
    query = re.sub(r"\s+(?:in my vault|for me|please)$", "", query, flags=re.I)
    query = _ARTICLE_RE.sub("", query)
    query = _FILE_PREFIX_RE.sub("", query)
    query = re.sub(
        r"^(?:a\s+)?(?:file|document|doc)\s+(?:called|named|titled)\s+", "", query, flags=re.I
    )
    return _clean_slot(query)


def _strip_lead_ins(text: str) -> str:
    """Remove conversational openers and trailing padding around a command."""
    for _ in range(5):
        before = text
        for pattern in _LEAD_IN_PATTERNS:
            text = re.sub(pattern, "", text, count=1, flags=re.I)
        text = text.strip()
        if text == before:
            break
    # Never trim a dictated note body: a note may legitimately end in a word
    # like "thanks".
    if not _NOTE_BODY_RE.search(text):
        text = _clean_query(text)
    return text


def _plan(intent, tier, audit_tool, speak, **extra):
    data = {
        "intent": intent,
        "tier": tier,
        "audit_tool": audit_tool,
        "speak": speak,
        "display": extra.pop("display", speak),
    }
    data.update(extra)
    return data


def _blocked(speak, reason="blocked"):
    return _plan(
        "blocked", TIER_BLOCKED, "voice_blocked",
        speak, display=speak, blocked_reason=reason,
    )


@dataclass(frozen=True)
class Tool:
    """One capability and every phrasing that reaches it.

    ``patterns`` are regular expressions searched in order. ``slots`` maps a
    named group to a plan key through an optional cleaner. ``build`` takes over
    when a tool needs branching (a note has a title and a body, an open command
    may have no name); it returns a complete plan or ``None`` to keep looking.
    """

    name: str
    audit: str
    speech: str
    tier: int = TIER_READ
    patterns: tuple = ()
    slots: tuple = ()
    defaults: dict | None = None
    action: dict | None = None
    build: Callable[[re.Match, str], dict | None] | None = None


def _run_tool(tool: Tool, text: str):
    """First plan produced by this tool, or None when nothing matches."""
    for pattern in tool.patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        if tool.build:
            return tool.build(match, text)
        extra = dict(tool.defaults or {})
        for group, key, cleaner in tool.slots:
            value = match.groupdict().get(group)
            if value is not None:
                extra[key] = cleaner(value) if cleaner else _clean_slot(value)
        if tool.action:
            extra["client_action"] = dict(tool.action)
        return _plan(tool.name, tool.tier, tool.audit, tool.speech, **extra)
    return None


def _match_tools(tools, text):
    for tool in tools:
        plan = _run_tool(tool, text)
        if plan is not None:
            return plan
    return None


# ---------------------------------------------------------------------------
# Small talk: a complete utterance, matched against the whole transcript.
# ---------------------------------------------------------------------------
_SMALL_TALK_TOOLS = (
    Tool(
        "small_talk", "voice_small_talk",
        "Hello. Tap the microphone and ask me to list or search files, open a "
        "file or folder, summarize your vault, list notes, contacts, or shares, "
        "create a note, or prepare a share.",
        patterns=(
            r"^(?:hi|hello|hey|yo|good morning|good afternoon|good evening)"
            r"(?:\s+(?:there|buffy|vault))?$",
        ),
    ),
    Tool(
        "small_talk", "voice_small_talk", "You're welcome.",
        patterns=(
            r"^(?:thanks|thank you|thanks a lot|thank you so much|cheers|great|"
            r"perfect|nice|good job|well done)$",
        ),
    ),
    Tool(
        "small_talk", "voice_small_talk",
        "I'm here and ready. Tap the microphone and tell me what you need.",
        patterns=(
            r"^(?:how are you|how'?s it going|how are things|are you there|"
            r"can you hear me|are you listening)$",
        ),
    ),
    Tool(
        "small_talk", "voice_small_talk",
        "I'm the vault assistant. I work with the files, notes, contacts, and "
        "share links in this vault, and I ask you to confirm before changing "
        "anything.",
        patterns=(
            r"^(?:who are you|what are you|what'?s your name|what is your name|"
            r"your name)$",
        ),
    ),
    Tool(
        "small_talk", "voice_small_talk",
        "Ready. Tap the microphone when you want to ask something.",
        patterns=(
            r"^(?:ok|okay|alright|got it|cool|sure|nothing|nothing else|"
            r"that'?s all|that is all)$",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Commands: confirmation, cancellation and help. These are answered before any
# navigation or lookup so "yes" never becomes a search for "yes".
# ---------------------------------------------------------------------------
_HELP_TEXT = (
    "You can ask me to list or search files, open a file or folder, tell you "
    "what is in a folder, where a file is, or when you added it, summarize "
    "your vault, list notes, contacts, or active shares, create a note, or "
    "prepare a share with a saved contact. You can also ask how to upload, "
    "share, or restore something. I will ask you to confirm before changing "
    "anything. I cannot delete files, change passwords, or accept an email "
    "address by voice."
)

_COMMAND_TOOLS = (
    Tool(
        "confirm", "voice_confirm", "Confirming the pending action.",
        tier=TIER_CONFIRM,
        patterns=(r"^(?:confirm|yes|yes please|go ahead|do it)$",),
    ),
    Tool(
        "cancel", "voice_cancel", "Cancelled.",
        patterns=(r"^(?:cancel|no|never mind|nevermind|stop|forget it)$",),
    ),
    Tool(
        "help", "voice_help", _HELP_TEXT,
        patterns=(
            r"\b(?:help|what can you do|what can i ask|which commands|how do i use)\b",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Navigation: read-only jumps. Deliberately checked before the refusal guard,
# so "show the trash" stays available while "empty the trash" is still refused.
# ---------------------------------------------------------------------------
_NAV_TOOLS = (
    Tool(
        "open_trash", "voice_open_trash", "Opening the trash.",
        patterns=(r"\b(?:open|show|view)\b.*\btrash\b",),
        action={"type": "open_trash"},
    ),
    Tool(
        "open_settings", "voice_open_settings", "Opening settings.",
        patterns=(r"\b(?:open|show)\b.*\bsettings?\b",),
        action={"type": "open_settings"},
    ),
    Tool(
        "open_shares", "voice_open_shares", "Opening your share links.",
        patterns=(r"\b(?:open|show|manage|list)\b.*\b(?:share links?|shares?)\b",),
        action={"type": "navigate", "url": "/shares"},
    ),
    Tool(
        "open_dashboard", "voice_open_dashboard", "Opening your dashboard.",
        patterns=(r"\b(?:open|go to|show)\b.*\b(?:dashboard|home)\b",),
        action={"type": "navigate", "url": "/dashboard"},
    ),
)

# ---------------------------------------------------------------------------
# Refusal guard. Not tools: these never reach a handler. A question is exempt
# (it asks for information and cannot change anything); only an imperative is
# refused, and the reason is recorded for the audit trail.
# ---------------------------------------------------------------------------
_BLOCKED_PATTERNS = (
    (
        r"\b(delete|purge|empty|erase)\b",
        "Deleting files, notes, or accounts is not available by voice. Use the "
        "interface so you can review the confirmation.",
        "forbidden_action",
    ),
    (
        r"\b(remove|trash)\b",
        "Moving or removing files is not available by voice. Use the interface "
        "so you can review the selection.",
        "forbidden_action",
    ),
    (
        r"\b(revoke|disable)\b.*\b(share|link)\b",
        "Revoking a share link is not available by voice. Open Manage shares to do that.",
        "forbidden_action",
    ),
    (
        r"\b(change|reset|update|forgot)\b.*\bpassword\b",
        "Password changes are not available by voice. Use Settings or the "
        "password-reset page.",
        "forbidden_action",
    ),
    (
        r"\b(delete|close)\b.*\baccount\b",
        "Account deletion is not available by voice. Use Settings so you can "
        "complete the password and phrase checks.",
        "forbidden_action",
    ),
    (
        r"\b(upload|attach)\b",
        "Uploading files is not available by voice. Use the Add files control.",
        "forbidden_action",
    ),
    (
        r"\b(send|compose|write)\b.*\bemail\b",
        "I do not send email by voice. Create a share in the interface and "
        "choose a saved contact.",
        "forbidden_action",
    ),
    (
        r"\b(show|reveal|tell|read)\b.*\b(password|secret|key)\b",
        "I cannot reveal passwords or encryption secrets.",
        "forbidden_action",
    ),
    (
        r"\b(?:read|speak|recite)\b.*\bnotes?\b"
        r"|\bwhat(?:'s| is)\s+(?:in|inside)\s+(?:my|the)\s+note\b",
        "I never read note contents aloud. Open the Notepad to read a note, or "
        "ask me to list your note titles.",
        "note_content",
    ),
)


def _check_blocked(low):
    """The refusal plan for an imperative, or None for a question/read."""
    if _QUESTION_START_RE.match(low):
        return None
    for pattern, message, reason in _BLOCKED_PATTERNS:
        if re.search(pattern, low):
            return _blocked(message, reason)
    return None


# ---------------------------------------------------------------------------
# Answers: questions about how the app works. Static text, no lookup, no
# side effects, so they can be spoken immediately.
# ---------------------------------------------------------------------------
_FAQ_ENTRIES = (
    (
        r"\b(?:how|where|can)\b.*\b(?:upload|add|import)\b.*\b(?:files?|documents?|pdfs?)\b",
        "Tap Add files on the dashboard, choose a folder, and pick the file. "
        "Uploads happen in the interface only; I cannot take a file by voice.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:share|sharing|link)\b",
        "Open a file and choose Share, or say: share passport dot pdf with "
        "Rahul. You can pick 10 minutes, 1 hour, or 24 hours, one time or "
        "multiple times. I never send email and never accept an email address "
        "by voice.",
    ),
    (
        r"\b(?:how|where|can)\b.*\bcontacts?\b",
        "Open Settings, then Saved contacts, and add the person's name and "
        "email. I can only share with a saved contact.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:delete|close|remove)\b.*\baccount\b",
        "Use Settings, then Delete account. It asks for your password and your "
        "phrase, and I cannot do that by voice.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:delete|remove|trash|restore|recover)\b",
        "Select the file and move it to Trash. Deleted files stay there for 30 "
        "days, and the Trash panel can restore them or empty the trash. I "
        "cannot delete anything by voice.",
    ),
    (
        r"\b(?:how|where|can)\b.*\bpassword\b",
        "Use Change password on the dashboard. I never take, speak, or change "
        "a password by voice.",
    ),
    (
        r"\b(?:how|is|are|does)\b.*\b(?:encrypt|encryption|encrypted|secure|"
        r"safe|protected|security)\b",
        "Files are encrypted before they are stored, and notes are encrypted "
        "too. Share links can be one-time or time-limited, and I never speak "
        "note contents, passwords, or keys.",
    ),
    (
        r"\b(?:how|where|can)\b.*\bnotes?\b",
        "Open the Notepad, or say: create a note titled Renewals with content "
        "passport expires in June. I ask you to confirm before saving, and I "
        "never read a note aloud.",
    ),
    (
        r"\b(?:how|can)\b.*\bfolders?\b",
        "Folders are on the dashboard. Say open the Finance folder, or ask what "
        "is in the Finance folder.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:download|export|backup)\b",
        "Open a file and choose Download. There is no cloud backup; your "
        "encrypted files stay in this vault.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:search|find|look for)\b",
        "Say find and a word from the name, for example find passport. I list "
        "the matches, and you can say open and the exact name.",
    ),
    (
        r"\b(?:how|where|can)\b.*\b(?:open|view|preview)\b",
        "Say open and the file name, for example open passport dot pdf. I open "
        "the preview for you, or suggest the closest file names if I mishear.",
    ),
    (
        r"\bwhat\b.*\b(?:is this app|is this site|does this app do|is secure vault)\b",
        "Secure Vault stores your files and notes encrypted, shares them with "
        "expiring links, and keeps a saved-contact list. Ask me to list files, "
        "open a folder, summarize the vault, create a note, or prepare a share.",
    ),
)

_ANSWER_TOOLS = tuple(
    Tool("faq", "voice_faq", answer, patterns=(pattern,))
    for pattern, answer in _FAQ_ENTRIES
)


# ---------------------------------------------------------------------------
# Builders for the tools that need branching rather than a plain slot.
# ---------------------------------------------------------------------------
def _build_document_info(match, _text):
    query = _info_slot(match.group(1))
    if not query:
        return None
    return _plan(
        "document_info", TIER_READ, "voice_document_info",
        "Here are the details for that file.",
        document_query=query,
    )


def _build_open_document(match, _text):
    query = _document_slot(match.group("query") or "")
    speech = "Opening that file." if query else "Opening the last file I found."
    return _plan(
        "open_document", TIER_READ, "voice_open_document",
        speech,
        document_query=query,
    )


def _build_note(match, _text):
    body = (match.group("body") or "").strip(" :")
    if not body:
        return _plan(
            "ask_note", TIER_READ, "voice_note_missing",
            "What should I call the note? Say something like: create a note "
            "titled Passwords with content my new password is in the vault.",
        )
    content = ""
    title = body
    content_match = re.match(
        r"^(?P<title>.+?)\s+(?:with\s+(?:the\s+)?content|saying|that\s+says|"
        r"containing|content)\s*:?\s*(?P<content>.+)$",
        body, re.I,
    )
    if content_match:
        title = content_match.group("title")
        content = content_match.group("content")
    title = _clean_slot(title)
    content = content.strip()
    if not title:
        return _plan(
            "ask_note", TIER_READ, "voice_note_missing",
            "What should I call the note?",
        )
    if len(title) > VOICE_MAX_NOTE_TITLE:
        return _plan(
            "error", TIER_READ, "voice_note_too_long",
            "That note title is too long. Please shorten it.",
        )
    if len(content) > VOICE_MAX_NOTE_CONTENT:
        return _plan(
            "error", TIER_READ, "voice_note_too_long",
            "That note is too long for me to safely repeat back. Please "
            "shorten it or type it in the Notepad.",
        )
    return _plan(
        "create_note", TIER_CONFIRM, "voice_create_note",
        "I can create that note after you confirm.",
        note_title=title,
        note_content=content,
    )


def _build_share(_match, text):
    body = re.sub(r"^(?:please\s+)?share\s+", "", text, flags=re.I).strip()
    split = re.search(r"\s+(?:with|to)\s+", body, re.I)
    if not split:
        return _plan(
            "error", TIER_READ, "voice_share_missing",
            "Say who the file is for, for example: share passport.pdf with "
            "Rahul, one time for one hour. Only saved contacts can be used.",
        )
    doc_part = _clean_slot(body[:split.start()])
    doc_part = re.sub(r"^(?:my|the)\s+", "", doc_part, flags=re.I)
    contact_part = body[split.end():].strip()
    contact_part = re.sub(
        r"^(?:my\s+)?(?:saved\s+)?contact\s+\s*", "", contact_part, flags=re.I
    )
    contact_part, expiry, usage, option_error = _extract_share_options(contact_part)
    if option_error:
        return _plan(
            "error", TIER_READ, "voice_share_bad_option",
            option_error,
        )
    docs_reference = doc_part
    if _is_context_reference(docs_reference):
        docs_reference = ""
    if not contact_part:
        return _plan(
            "error", TIER_READ, "voice_share_missing",
            "Which saved contact should receive it? Add contacts in Settings "
            "if the person is not listed.",
        )
    return _plan(
        "share_document", TIER_SENSITIVE, "voice_share_prepare",
        "I can prepare that share after you confirm.",
        document_query=docs_reference,
        contact_query=_clean_slot(contact_part),
        expiry=expiry,
        usage=usage,
        expiry_label=_EXPIRY_LABELS[expiry],
        usage_label=_USAGE_LABELS[usage],
    )


def _extract_share_options(text: str):
    """Return (cleaned_text, expiry, usage, error)."""
    cleaned = text
    low = cleaned.lower()
    expiry = "1h"
    usage = "once"
    error = None

    if re.search(r"\b(one[-\s]?time|single[-\s]?use|once|one download)\b", low):
        usage = "once"
    if re.search(r"\b(multiple[-\s]?times|multi[-\s]?use|unlimited|many times)\b", low):
        usage = "multi"

    if re.search(r"\b(10|ten)\s*(minutes?|mins?)\b", low):
        expiry = "10m"
    elif re.search(r"\b(24|twenty[-\s]?four)\s*(hours?|hrs?)\b", low) or re.search(r"\b(1|one)\s*day\b", low):
        expiry = "24h"
    elif re.search(r"\b(1|one|an)\s*(hour|hr)\b", low):
        expiry = "1h"
    elif re.search(r"\b(7|seven)\s*(days?)\b", low) or re.search(r"\b(a|one)\s*week\b", low):
        error = "I can create links for 10 minutes, 1 hour, or 24 hours."

    # Remove schedule words from the recipient name.
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:10|ten)\s*(?:minutes?|mins?)\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:24|twenty[-\s]?four)\s*(?:hours?|hrs?)\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:1|one|an)\s*(?:hour|hr)\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:1|one)\s*day\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:7|seven)\s*days?\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(?:for\s+)?(?:a|one)\s*week\b", " ", cleaned, flags=re.I
    )
    cleaned = re.sub(
        r"\b(one[-\s]?time|single[-\s]?use|once|one download|"
        r"multiple[-\s]?times|multi[-\s]?use|unlimited|many times)\b",
        " ", cleaned, flags=re.I
    )
    cleaned = re.sub(r"\b(?:for|please)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.")
    return cleaned, expiry, usage, error


# ---------------------------------------------------------------------------
# Read tools. One tool understands every phrasing of the same question, so
# "what files do I have", "list my files" and "show me my files" all land on
# list_documents. Order is priority order.
# ---------------------------------------------------------------------------
_READ_TOOLS = (
    Tool(
        "list_folder_contents", "voice_list_folder_contents",
        "Here is what is in that folder.",
        patterns=(
            r"^(?:what(?:'s| is| are)|which (?:files|documents) are)\s+(?:in|inside)\s+"
            r"(?:my\s+|the\s+)?(?P<folder_query>.+?)\s+folder$",
            r"^(?:list|show)(?:\s+me)?\s+(?:the\s+|all\s+|my\s+)*(?:files|documents)\s+"
            r"(?:in|inside)\s+(?:my\s+|the\s+)?(?P<folder_query>.+?)(?:\s+folder)?$",
            r"^how many\s+(?:files|documents)\s+(?:are\s+)?(?:in|inside)\s+"
            r"(?:my\s+|the\s+)?(?P<folder_query>.+?)(?:\s+folder)?$",
        ),
        slots=(("folder_query", "folder_query", _folder_slot),),
    ),
    Tool(
        "vault_summary", "voice_summary", "Here is your vault summary.",
        patterns=(
            r"\b(?:summary|overview|what'?s in my vault|"
            r"how many (?:files|documents|notes|contacts|shares))\b",
        ),
    ),
    Tool(
        "list_documents", "voice_list_documents",
        "Here are your most recent files.",
        patterns=(
            r"\b(?:recent|latest|newest)\b.*\b(?:files?|documents?|uploads?)\b",
            r"^(?:recent|latest|newest)\s+(?:files?|documents?|uploads?)$",
            r"\b(?:add|added|upload|uploaded|saved)\b.*\b(?:recently|lately)\b"
            r"|\b(?:recently|lately)\b.*\b(?:add|added|upload|uploaded|saved)\b",
        ),
    ),
    Tool(
        "list_documents", "voice_list_documents", "Here is the file count.",
        patterns=(r"^how many\s+(?:documents|files)\b",),
    ),
    Tool(
        "list_documents", "voice_list_documents", "Here are your files.",
        patterns=(
            r"^(?:please\s+)?(?:list|show|what are|what'?s in)\s+(?:me\s+)?"
            r"(?:all\s+)?(?:my\s+)?(?:documents|files)\b",
            r"^(?:what|which)\s+(?:documents|files)\s+"
            r"(?:do\s+i\s+have|are there|have i got)\b",
            r"^(?:open|show)\s+(?:me\s+)?(?:my\s+)?(?:documents|files)$",
            r"^(?:my\s+)?(?:documents|files)$",
        ),
    ),
    Tool(
        "list_folders", "voice_list_folders", "Here are your folders.",
        patterns=(
            r"^(?:please\s+)?(?:list|show)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?folders?\b",
            r"^(?:what|which)\s+folders?"
            r"(?:\s+(?:do\s+i\s+have|are\s+there|have\s+i\s+got|exist))?$",
            r"^how many\s+folders?\b",
            r"^(?:my\s+)?folders?$",
        ),
    ),
    Tool(
        "list_notes", "voice_list_notes", "Here are your note titles.",
        patterns=(
            r"^(?:please\s+)?(?:list|show)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?notes?\b",
            r"^(?:what|which)\s+notes?\b",
            r"^how many\s+notes?\b",
            r"^(?:my\s+)?notes?$",
        ),
    ),
    Tool(
        "list_contacts", "voice_list_contacts", "Here are your saved contacts.",
        patterns=(
            r"^(?:please\s+)?(?:list|show|who are|who's|what are|which are)\s+"
            r"(?:me\s+)?(?:my\s+)?(?:saved\s+)?contacts?\b",
            r"^(?:what|which)\s+(?:saved\s+)?contacts?\b",
            r"^(?:my\s+)?(?:saved\s+)?contacts?$",
        ),
    ),
    Tool(
        "list_shares", "voice_list_shares", "Here are your active share links.",
        patterns=(
            r"^(?:what|which)\s+shares?\b",
            r"\b(?:active|current|live)\b.*\bshare links?\b",
            r"^(?:active\s+|my\s+)?shares?$",
        ),
    ),
    Tool(
        "open_folder", "voice_open_folder", "Opening that folder.",
        defaults={"folder_query": ""},
        patterns=(
            r"^(?:please\s+)?(?:open|go to|show)\s+(?:the\s+)?folder$",
            r"^(?:please\s+)?(?:open|go to|show)\s+(?:the\s+)?folder\s+"
            r"(?P<folder_query>.+?)(?:\s+folder)?$",
            r"^(?:please\s+)?(?:open|go to)\s+(?:the\s+)?(?P<folder_query>.+?)\s+folder$",
            r"^(?:where(?:'s| is)|which folder is|what folder is)\s+"
            r"(?:the\s+|my\s+)?(?P<folder_query>.+?)\s+folder$",
        ),
        slots=(("folder_query", "folder_query", _folder_slot),),
    ),
    Tool(
        "document_info", "voice_document_info", "Here are the details for that file.",
        patterns=(
            r"^(?:where is|where'?s|where can i find|where did i put)\s+(.+)$",
            r"^(?:which|what)\s+folder\s+(?:is|has|contains)\s+(.+?)(?:\s+in)?$",
            r"^when did i (?:add|upload|create|save)\s+(.+)$",
            r"^what date did i (?:add|upload|create|save)\s+(.+)$",
            r"^how (?:big|large) is\s+(.+)$",
            r"^what size is\s+(.+)$",
            r"^tell me about\s+(?:my\s+|the\s+)?(?:file|document)?\s*(.+)$",
        ),
        build=_build_document_info,
    ),
    Tool(
        "search_documents", "voice_search_documents", "Searching your files.",
        patterns=(
            r"^(?:please\s+)?(?:find|search(?:\s+for)?|look for|do i have|"
            r"where is|where'?s)\s+(?P<query>.+)$",
        ),
        slots=(("query", "query", _search_slot),),
    ),
    Tool(
        "create_note", "voice_create_note", "I can create that note after you confirm.",
        tier=TIER_CONFIRM,
        patterns=(
            r"^(?:please\s+)?(?:create|add|make|write|take)\s+(?:a\s+)?note"
            r"(?:\s+(?:titled|called|named))?\s*(?P<body>.*)$",
        ),
        build=_build_note,
    ),
    Tool(
        "share_document", "voice_share_prepare",
        "I can prepare that share after you confirm.",
        tier=TIER_SENSITIVE,
        patterns=(r"^(?:please\s+)?share\b",),
        build=_build_share,
    ),
    Tool(
        "open_document", "voice_open_document", "Opening that file.",
        patterns=(
            r"^(?:please\s+)?(?:open|show|preview|view|display)\s+(?:me\s+)?"
            r"(?:the\s+)?(?:file|document|doc|pdf|image)?\s*(?P<query>.*)$",
        ),
        build=_build_open_document,
    ),
)

# ---------------------------------------------------------------------------
# Last-resort topic routing: a question that matched no tool still gets a
# useful read-only answer when it mentions a topic the vault knows about.
# ---------------------------------------------------------------------------
_FALLBACK_TOOLS = (
    Tool(
        "open_trash", "voice_open_trash", "Opening the trash.",
        patterns=(r"\btrash\b|\brecycle\b|\bdeleted\b",),
        action={"type": "open_trash"},
    ),
    Tool(
        "open_settings", "voice_open_settings", "Opening settings.",
        patterns=(r"\bsettings?\b|\bprofile\b|\baccount\b",),
        action={"type": "open_settings"},
    ),
    Tool(
        "list_shares", "voice_list_shares", "Here are your share links.",
        patterns=(r"\bshares?\b|\bshare links?\b",),
    ),
    Tool(
        "list_contacts", "voice_list_contacts", "Here are your saved contacts.",
        patterns=(r"\bcontacts?\b|\brecipients?\b",),
    ),
    Tool(
        "list_notes", "voice_list_notes", "Here are your note titles.",
        patterns=(r"\bnotes?\b|\bnotepad\b",),
    ),
    Tool(
        "list_folders", "voice_list_folders", "Here are your folders.",
        patterns=(r"\bfolders?\b",),
    ),
    Tool(
        "list_documents", "voice_list_documents", "Here are your files.",
        patterns=(
            r"\bfiles?\b|\bdocuments?\b|\bdocs?\b|\bpdfs?\b|\buploads?\b|\bimages?\b",
        ),
    ),
    Tool(
        "vault_summary", "voice_summary", "Here is your vault summary.",
        patterns=(r"\bvault\b|\bstorage\b|\bspace\b|\bhow many\b",),
    ),
)

_EMPTY_SPEECH = "I didn't hear anything. Tap the microphone and try again."
_LOST_SPEECH = "I didn't catch a command I know. Say help to hear what I can do."
_UNKNOWN_SPEECH = (
    "I'm not sure how to answer that. I can list or search files, open a file "
    "or folder, tell you what is in a folder or where a file is, summarize "
    "your vault, list notes, contacts, or shares, create a note, or prepare a "
    "share. Say help for examples."
)


def parse_voice_command(text: str):
    """Turn an English transcript into a plan without touching the database.

    Database lookups and the final owner-scope checks happen in the route
    layer. This function is intentionally side-effect free so it is easy to
    test and audit.
    """
    original = normalize_transcript(text)
    if not original:
        return _plan("empty", TIER_READ, "voice_empty", _EMPTY_SPEECH)

    if _EMAIL_RE.search(original):
        return _blocked(
            "I can only share with a saved contact. I never accept an email "
            "address by voice. Add the person in Settings first, then ask me "
            "to share with their saved contact name.",
            "dictated_email",
        )

    # A greeting is a complete utterance, so answer it before the polite
    # padding is stripped away.
    short = re.sub(r"[\s.!?,]+$", "", original.lower())
    plan = _match_tools(_SMALL_TALK_TOOLS, short)
    if plan is not None:
        return plan

    # Tolerate polite or indirect phrasing ("Can you please open ...",
    # "I want you to open ...") before matching any command.
    original = _strip_lead_ins(original)
    if not original:
        return _plan("unknown", TIER_READ, "voice_unknown", _LOST_SPEECH)
    low = original.lower()

    # Confirmation, cancellation and help are resolved first: they must not be
    # mistaken for a file name or a topic.
    plan = _match_tools(_COMMAND_TOOLS, low)
    if plan is not None:
        return plan

    # Navigation stays available even though its words overlap the refusals.
    plan = _match_tools(_NAV_TOOLS, low)
    if plan is not None:
        return plan

    plan = _check_blocked(low)
    if plan is not None:
        return plan

    # "How many ..." is a count, so it skips the how-to answers and reaches the
    # list tools.
    if not _COUNT_RE.match(low):
        plan = _match_tools(_ANSWER_TOOLS, low)
        if plan is not None:
            return plan

    plan = _match_tools(_READ_TOOLS, original)
    if plan is not None:
        return plan

    plan = _match_tools(_FALLBACK_TOOLS, low)
    if plan is not None:
        return plan

    return _plan("unknown", TIER_READ, "voice_unknown", _UNKNOWN_SPEECH)
