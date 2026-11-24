"""Safe, deterministic command planning for the tap-to-talk assistant.

The browser owns speech-to-text and text-to-speech. This module receives only
the recognized English text and turns it into a small plan. It never calls an
external model and it never accepts an email address, a password, or a
destructive instruction from the microphone.
"""
from __future__ import annotations

import os
import re

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


def normalize_transcript(text: str) -> str:
    """Trim, collapse whitespace, and cap the text before parsing."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:VOICE_MAX_TRANSCRIPT]


def _clean_slot(value: str) -> str:
    return (value or "").strip(" \t\r\n.,!?;:\"'")


def _is_context_reference(value: str) -> bool:
    value = _clean_slot(value).lower()
    value = re.sub(r"^(?:the|that|this)\s+", "", value)
    return value in {"", "it", "that", "this", "file", "document", "one"}


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


def parse_voice_command(text: str):
    """Turn an English transcript into a plan without touching the database.

    Database lookups and the final owner-scope checks happen in the route
    layer. This function is intentionally side-effect free so it is easy to
    test and audit.
    """
    original = normalize_transcript(text)
    if not original:
        return _plan(
            "empty", TIER_READ, "voice_empty",
            "I didn't hear anything. Tap the microphone and try again.",
        )
    low = original.lower()

    if _EMAIL_RE.search(original):
        return _blocked(
            "I can only share with a saved contact. I never accept an email "
            "address by voice. Add the person in Settings first, then ask me "
            "to share with their saved contact name.",
            "dictated_email",
        )

    # Confirmation and cancellation are handled only when a server-side
    # pending action exists. The route layer enforces that.
    if re.fullmatch(r"(?:confirm|yes|yes please|go ahead|do it)", low):
        return _plan(
            "confirm", TIER_CONFIRM, "voice_confirm",
            "Confirming the pending action.",
        )
    if re.fullmatch(r"(?:cancel|no|never mind|nevermind|stop|forget it)", low):
        return _plan(
            "cancel", TIER_READ, "voice_cancel",
            "Cancelled.",
        )

    if re.search(r"\b(help|what can you do|which commands|how do i use)\b", low):
        return _plan(
            "help", TIER_READ, "voice_help",
            "You can ask me to list or search files, open a file or folder, "
            "summarize your vault, list saved contacts, create a note, or "
            "prepare a share with a saved contact. I will ask you to confirm "
            "before changing anything. I cannot delete files, change "
            "passwords, or accept an email address by voice.",
        )

    # Read-only navigation intents. These are handled before destructive
    # keyword checks so "show the trash" remains available.
    if re.search(r"\b(open|show|view)\b.*\btrash\b", low):
        return _plan(
            "open_trash", TIER_READ, "voice_open_trash",
            "Opening the trash.",
            client_action={"type": "open_trash"},
        )
    if re.search(r"\b(open|show)\b.*\bsettings\b", low):
        return _plan(
            "open_settings", TIER_READ, "voice_open_settings",
            "Opening settings.",
            client_action={"type": "open_settings"},
        )
    if re.search(r"\b(open|show|manage|list)\b.*\b(share links?|shares?)\b", low):
        return _plan(
            "open_shares", TIER_READ, "voice_open_shares",
            "Opening your share links.",
            client_action={"type": "navigate", "url": "/shares"},
        )
    if re.search(r"\b(open|go to|show)\b.*\b(dashboard|home)\b", low):
        return _plan(
            "open_dashboard", TIER_READ, "voice_open_dashboard",
            "Opening your dashboard.",
            client_action={"type": "navigate", "url": "/dashboard"},
        )

    # Destructive, authentication, and outbound-email actions stay in the UI.
    forbidden = (
        (r"\b(delete|purge|empty|erase)\b", "Deleting files, notes, or accounts is not available by voice. Use the interface so you can review the confirmation."),
        (r"\b(remove|trash)\b", "Moving or removing files is not available by voice. Use the interface so you can review the selection."),
        (r"\b(revoke|disable)\b.*\b(share|link)\b", "Revoking a share link is not available by voice. Open Manage shares to do that."),
        (r"\b(change|reset|update|forgot)\b.*\bpassword\b", "Password changes are not available by voice. Use Settings or the password-reset page."),
        (r"\b(delete|close)\b.*\baccount\b", "Account deletion is not available by voice. Use Settings so you can complete the password and phrase checks."),
        (r"\b(upload|attach)\b", "Uploading files is not available by voice. Use the Add files control."),
        (r"\b(send|compose|write)\b.*\bemail\b", "I do not send email by voice. Create a share in the interface and choose a saved contact."),
        (r"\b(show|reveal|tell|read)\b.*\b(password|secret|key)\b", "I cannot reveal passwords or encryption secrets."),
    )
    for pattern, message in forbidden:
        if re.search(pattern, low):
            return _blocked(message, "forbidden_action")

    # Summary
    if re.search(r"\b(summary|overview|what'?s in my vault|how many (?:files|documents|notes|contacts|shares))\b", low):
        return _plan(
            "vault_summary", TIER_READ, "voice_summary",
            "Here is your vault summary.",
        )

    # List documents
    if re.match(r"^(?:please\s+)?(?:list|show|what are|what'?s in)\s+(?:all\s+)?(?:my\s+)?(?:documents|files)\b", low):
        return _plan(
            "list_documents", TIER_READ, "voice_list_documents",
            "Here are your files.",
        )
    if re.match(r"^how many\s+(?:documents|files)\b", low):
        return _plan(
            "list_documents", TIER_READ, "voice_list_documents",
            "Here is the file count.",
        )

    # List folders
    if re.match(r"^(?:please\s+)?(?:list|show)\s+(?:my\s+)?folders?\b", low):
        return _plan(
            "list_folders", TIER_READ, "voice_list_folders",
            "Here are your folders.",
        )

    # List notes (titles only; note text is never sent through the assistant)
    if re.match(r"^(?:please\s+)?(?:list|show)\s+(?:my\s+)?notes?\b", low):
        return _plan(
            "list_notes", TIER_READ, "voice_list_notes",
            "Here are your note titles.",
        )

    # List saved contacts (names only)
    if re.match(r"^(?:please\s+)?(?:list|show|who are)\s+(?:my\s+)?(?:saved\s+)?contacts?\b", low):
        return _plan(
            "list_contacts", TIER_READ, "voice_list_contacts",
            "Here are your saved contacts.",
        )

    # Open folder
    folder_match = re.match(
        r"^(?:please\s+)?(?:open|go to|show)\s+(?:the\s+)?folder\s+(.+?)(?:\s+folder)?$",
        original, re.I,
    )
    if folder_match:
        return _plan(
            "open_folder", TIER_READ, "voice_open_folder",
            "Opening that folder.",
            folder_query=_clean_slot(folder_match.group(1)),
        )
    folder_match = re.match(
        r"^(?:please\s+)?(?:open|go to)\s+(?:the\s+)?(.+?)\s+folder$",
        original, re.I,
    )
    if folder_match:
        return _plan(
            "open_folder", TIER_READ, "voice_open_folder",
            "Opening that folder.",
            folder_query=_clean_slot(folder_match.group(1)),
        )

    # Search
    search_match = re.match(
        r"^(?:please\s+)?(?:find|search(?:\s+for)?|look for|do i have|where is|where'?s)\s+(.+)$",
        original, re.I,
    )
    if search_match:
        query = _clean_slot(search_match.group(1))
        query = re.sub(r"\s+(?:in my vault|for me|please)$", "", query, flags=re.I)
        return _plan(
            "search_documents", TIER_READ, "voice_search_documents",
            "Searching your files.",
            query=query,
        )

    # Create note
    note_match = re.match(
        r"^(?:please\s+)?(?:create|add|make|write|take)\s+(?:a\s+)?note"
        r"(?:\s+(?:titled|called|named))?\s*(.*)$",
        original, re.I,
    )
    if note_match:
        body = note_match.group(1).strip(" :")
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

    # Share with a saved contact. The parser never extracts an email address.
    if re.match(r"^(?:please\s+)?share\b", original, re.I):
        body = re.sub(r"^(?:please\s+)?share\s+", "", original, flags=re.I).strip()
        split = re.search(r"\s+(?:with|to)\s+", body, re.I)
        if not split:
            return _plan(
                "error", TIER_READ, "voice_share_missing",
                "Say who the file is for, for example: share passport.pdf with "
                "Rahul, one time for one hour. Only saved contacts can be used.",
            )
        doc_part = _clean_slot(body[:split.start()])
        contact_part = body[split.end():].strip()
        contact_part = re.sub(
            r"^(?:my\s+)?(?:saved\s+)?contact\s+", "", contact_part, flags=re.I
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

    # Open document
    open_match = re.match(
        r"^(?:please\s+)?(?:open|show|preview|view|display)\s+"
        r"(?:me\s+)?(?:the\s+)?(?:file|document|doc|pdf|image)?\s*(.*)$",
        original, re.I,
    )
    if open_match:
        query = _clean_slot(open_match.group(1))
        if _is_context_reference(query):
            query = ""
        if not query:
            return _plan(
                "open_document", TIER_READ, "voice_open_document",
                "Opening the last file I found.",
                document_query="",
            )
        return _plan(
            "open_document", TIER_READ, "voice_open_document",
            "Opening that file.",
            document_query=query,
        )

    return _plan(
        "unknown", TIER_READ, "voice_unknown",
        "I didn't catch a command I know. Say help to hear what I can do.",
    )
