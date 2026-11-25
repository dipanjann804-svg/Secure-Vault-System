"""Tap-to-talk voice assistant routes.

Security boundaries:
  * The browser performs speech recognition; the server receives text only.
  * Read-only commands execute immediately.
  * Writes create a short-lived, encrypted server-side pending action. The
    client must present its nonce and the server revalidates ownership, live
    document state, and allowed options before executing.
  * Destructive, credential, upload, and outbound-email actions are refused.
  * No transcript, note text, email body, or audio is written to logs/context.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from encryption import decrypt_note, encrypt_note
from models import Contact, Document, Note, ShareLink, SecurityLog, VoiceContext, db
from voice_service import (
    VOICE_CONTEXT_TTL_SECONDS,
    VOICE_LANGUAGE,
    VOICE_PENDING_TTL_SECONDS,
    VOICE_RATE_LIMIT,
    VOICE_RATE_WINDOW_SECONDS,
    normalize_transcript,
    parse_voice_command,
)


def _now():
    return datetime.utcnow()


def _response(speak, display=None, status="ok", **extra):
    payload = {
        "status": status,
        "speak": speak,
        "display": display or speak,
    }
    payload.update(extra)
    return payload


def _audit(tool, outcome, status="SUCCESS"):
    """Queue an audit row without any transcript or note content."""
    db.session.add(SecurityLog(
        user_id=current_user.id,
        login_id=None,
        ip_address=(request.remote_addr or "")[:45] or None,
        status=status,
        user_agent=(request.user_agent.string or "")[:255] or None,
        tool=(tool or "voice")[:60],
        outcome=(outcome or "")[:30] or None,
    ))


def _get_context():
    ctx = VoiceContext.query.filter_by(user_id=current_user.id).first()
    if ctx is None:
        ctx = VoiceContext(user_id=current_user.id)
        db.session.add(ctx)
        try:
            db.session.flush()
        except Exception:
            db.session.rollback()
            ctx = VoiceContext.query.filter_by(user_id=current_user.id).first()
    return ctx


def _context_data(ctx):
    if not ctx or not ctx.context_json:
        return {}
    if ctx.updated_at and (_now() - ctx.updated_at).total_seconds() > VOICE_CONTEXT_TTL_SECONDS:
        ctx.context_json = None
        return {}
    try:
        value = json.loads(ctx.context_json)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _remember_documents(ctx, docs, intent):
    ids = [doc.id for doc in docs][:10]
    ctx.context_json = json.dumps({"last_document_ids": ids, "last_intent": intent})
    ctx.updated_at = _now()


def _context_documents(ctx):
    ids = _context_data(ctx).get("last_document_ids") or []
    ids = [int(value) for value in ids if isinstance(value, int) or str(value).isdigit()]
    if not ids:
        return []
    rows = Document.query.filter(
        Document.user_id == current_user.id,
        Document.deleted_at.is_(None),
        Document.id.in_(ids),
    ).all()
    by_id = {doc.id: doc for doc in rows}
    return [by_id[doc_id] for doc_id in ids if doc_id in by_id]


def _rate_limited(ctx):
    now = _now()
    if not ctx.window_started_at or (now - ctx.window_started_at).total_seconds() >= VOICE_RATE_WINDOW_SECONDS:
        ctx.window_started_at = now
        ctx.command_count = 0
    if (ctx.command_count or 0) >= VOICE_RATE_LIMIT:
        return True
    ctx.command_count = (ctx.command_count or 0) + 1
    ctx.updated_at = now
    return False


def _pending_state(ctx):
    if not ctx or not ctx.pending_nonce or not ctx.pending_action:
        return None
    if not ctx.pending_expires_at or ctx.pending_expires_at <= _now():
        ctx.pending_action = None
        ctx.pending_nonce = None
        ctx.pending_summary = None
        ctx.pending_expires_at = None
        return None
    seconds = max(0, int((ctx.pending_expires_at - _now()).total_seconds()))
    return {
        "nonce": ctx.pending_nonce,
        "summary": ctx.pending_summary or "A pending action is waiting.",
        "expires_in": seconds,
    }


def _set_pending(ctx, action, summary):
    ctx.pending_action = encrypt_note(json.dumps(action, separators=(",", ":")))
    ctx.pending_nonce = secrets.token_urlsafe(18)
    ctx.pending_summary = (summary or "")[:255]
    ctx.pending_expires_at = _now() + timedelta(seconds=VOICE_PENDING_TTL_SECONDS)
    ctx.updated_at = _now()
    return _pending_state(ctx)


def _clear_pending(ctx):
    ctx.pending_action = None
    ctx.pending_nonce = None
    ctx.pending_summary = None
    ctx.pending_expires_at = None
    ctx.updated_at = _now()


def _load_pending(ctx):
    if not ctx or not ctx.pending_action:
        return None
    try:
        action = json.loads(decrypt_note(ctx.pending_action))
        return action if isinstance(action, dict) else None
    except Exception:
        return None


def _claim_pending(nonce):
    """Atomically clear a pending action, returning its payload once.

    The nonce is never trusted as the action itself: the payload comes from
    the encrypted server-side row. A conditional update makes double-confirm
    races fail safely.
    """
    ctx = VoiceContext.query.filter_by(user_id=current_user.id).first()
    if not ctx or not ctx.pending_nonce or not ctx.pending_action:
        return None, "There is no pending action to confirm."
    if not nonce or not secrets.compare_digest(str(ctx.pending_nonce), str(nonce)):
        return None, "That confirmation is no longer valid. Please ask again."
    if not ctx.pending_expires_at or ctx.pending_expires_at <= _now():
        _clear_pending(ctx)
        db.session.commit()
        return None, "That pending action expired. Please ask again."

    action = _load_pending(ctx)
    if not action:
        _clear_pending(ctx)
        db.session.commit()
        return None, "That pending action could not be read. Please ask again."

    claimed = VoiceContext.query.filter(
        VoiceContext.user_id == current_user.id,
        VoiceContext.pending_nonce == ctx.pending_nonce,
        VoiceContext.pending_expires_at > _now(),
    ).update({
        VoiceContext.pending_action: None,
        VoiceContext.pending_nonce: None,
        VoiceContext.pending_summary: None,
        VoiceContext.pending_expires_at: None,
        VoiceContext.updated_at: _now(),
    }, synchronize_session=False)
    db.session.commit()
    if claimed != 1:
        return None, "That confirmation was already used. Please ask again."
    return action, None


def _find_documents(query, ctx):
    if not query:
        return _context_documents(ctx)
    query = query.strip()
    exact = Document.query.filter(
        Document.user_id == current_user.id,
        Document.deleted_at.is_(None),
        func.lower(Document.original_filename) == query.lower(),
    ).order_by(Document.created_at.desc()).limit(8).all()
    if exact:
        return exact
    like = f"%{query}%"
    return Document.query.filter(
        Document.user_id == current_user.id,
        Document.deleted_at.is_(None),
        or_(Document.original_filename.ilike(like), Document.folder.ilike(like)),
    ).order_by(Document.created_at.desc()).limit(8).all()


def _find_contacts(query):
    query = (query or "").strip()
    if not query:
        return []
    exact = Contact.query.filter(
        Contact.user_id == current_user.id,
        func.lower(Contact.name) == query.lower(),
    ).order_by(Contact.name.asc()).limit(8).all()
    if exact:
        return exact
    return Contact.query.filter(
        Contact.user_id == current_user.id,
        Contact.name.ilike(f"%{query}%"),
    ).order_by(Contact.name.asc()).limit(8).all()


def _folder_names():
    defaults = list(current_app.config.get("VOICE_DEFAULT_FOLDERS", []))
    hidden = set()
    try:
        hidden_value = json.loads(current_user.hidden_folders or "[]")
        if isinstance(hidden_value, list):
            hidden = {str(name).lower() for name in hidden_value if isinstance(name, str)}
    except (TypeError, ValueError):
        pass
    rows = db.session.query(Document.folder).filter(
        Document.user_id == current_user.id,
        Document.deleted_at.is_(None),
    ).distinct().all()
    names = defaults + [row[0] for row in rows if row[0]]
    seen = set()
    result = []
    for name in names:
        key = name.lower()
        if key not in seen and key not in hidden:
            seen.add(key)
            result.append(name)
    return result


def _list_line(doc):
    return f"{doc.original_filename} ({doc.folder})"


def _summary_payload():
    docs = Document.query.filter_by(user_id=current_user.id, deleted_at=None).count()
    notes = Note.query.filter_by(user_id=current_user.id).count()
    contacts = Contact.query.filter_by(user_id=current_user.id).count()
    active_links = ShareLink.query.filter(
        ShareLink.user_id == current_user.id,
        ShareLink.revoked_at.is_(None),
        or_(ShareLink.expires_at.is_(None), ShareLink.expires_at > _now()),
        or_(ShareLink.max_uses.is_(None), ShareLink.use_count < ShareLink.max_uses),
    ).count()
    folders = len(_folder_names())
    speak = (
        f"You have {docs} file{'s' if docs != 1 else ''}, "
        f"{notes} note{'s' if notes != 1 else ''}, "
        f"{contacts} saved contact{'s' if contacts != 1 else ''}, "
        f"{active_links} active share link{'s' if active_links != 1 else ''}, "
        f"and {folders} folder{'s' if folders != 1 else ''}."
    )
    return _response(speak)


def _handle_read(plan, ctx):
    intent = plan["intent"]

    if intent == "help":
        return _response(plan["speak"])

    if intent == "vault_summary":
        return _summary_payload()

    if intent == "list_documents":
        docs = Document.query.filter_by(
            user_id=current_user.id, deleted_at=None
        ).order_by(Document.created_at.desc()).all()
        _remember_documents(ctx, docs[:10], intent)
        if not docs:
            return _response("You do not have any files in your vault yet.")
        lines = [f"You have {len(docs)} file{'s' if len(docs) != 1 else ''}."]
        if docs:
            lines.append("The most recent are: " + "; ".join(_list_line(doc) for doc in docs[:5]) + ".")
        return _response(" ".join(lines), "\n".join(lines), data={
            "documents": [
                {"id": doc.id, "name": doc.original_filename, "folder": doc.folder}
                for doc in docs[:10]
            ]
        })

    if intent == "search_documents":
        query = plan.get("query") or ""
        if not query:
            return _response("What should I search for?")
        docs = _find_documents(query, ctx)
        _remember_documents(ctx, docs, intent)
        if not docs:
            return _response(f"I couldn't find a file matching {query}.", data={"documents": []})
        lines = [
            f"I found {len(docs)} file{'s' if len(docs) != 1 else ''} matching {query}: "
            + "; ".join(_list_line(doc) for doc in docs[:5]) + "."
        ]
        return _response(" ".join(lines), "\n".join(lines), data={
            "documents": [
                {"id": doc.id, "name": doc.original_filename, "folder": doc.folder}
                for doc in docs[:10]
            ]
        })

    if intent == "open_document":
        query = plan.get("document_query") or ""
        docs = _find_documents(query, ctx)
        if not docs:
            if not query:
                return _response("Which file should I open? Tell me its name first.")
            return _response(f"I couldn't find a file matching {query}.")
        if len(docs) > 1:
            names = "; ".join(_list_line(doc) for doc in docs[:5])
            _remember_documents(ctx, docs, intent)
            return _response(
                f"I found several matches: {names}. Please say the exact file name.",
                data={"documents": [
                    {"id": doc.id, "name": doc.original_filename, "folder": doc.folder}
                    for doc in docs
                ]},
            )
        doc = docs[0]
        _remember_documents(ctx, docs, intent)
        return _response(
            f"Opening {doc.original_filename} from {doc.folder}.",
            client_action={"type": "open_preview", "doc_id": doc.id},
        )

    if intent == "list_folders":
        names = _folder_names()
        if not names:
            return _response("You do not have any folders yet.")
        return _response(
            f"You have {len(names)} folders: " + "; ".join(names) + ".",
            data={"folders": names},
        )

    if intent == "open_folder":
        query = (plan.get("folder_query") or "").strip()
        names = _folder_names()
        if not query:
            if not names:
                return _response("You do not have any folders yet.")
            return _response(
                "Which folder? Your folders are: " + "; ".join(names) + "."
            )
        if not names:
            return _response("You do not have any folders yet.")
        match = next((name for name in names if name.lower() == query.lower()), None)
        if not match:
            matches = [name for name in names if query.lower() in name.lower()] if query else []
            if len(matches) == 1:
                match = matches[0]
        if not match:
            return _response(
                f"I couldn't find a folder named {query}. Your folders are: "
                + "; ".join(names) + "."
            )
        return _response(
            f"Opening the {match} folder.",
            client_action={"type": "navigate", "url": f"/folders/{quote(match, safe='')}"},
        )

    if intent == "list_notes":
        notes = Note.query.filter_by(user_id=current_user.id).order_by(
            Note.created_at.desc()
        ).all()
        if not notes:
            return _response("You do not have any notes yet.")
        titles = [note.title or "Untitled Note" for note in notes[:5]]
        return _response(
            f"You have {len(notes)} note{'s' if len(notes) != 1 else ''}. "
            "The most recent titles are: " + "; ".join(titles) + ".",
            data={"notes": [{"id": note.id, "title": note.title or "Untitled Note"} for note in notes[:5]]},
        )

    if intent == "list_contacts":
        contacts = Contact.query.filter_by(user_id=current_user.id).order_by(
            Contact.name.asc()
        ).all()
        if not contacts:
            return _response(
                "You do not have any saved contacts. Add one in Settings before "
                "asking me to share."
            )
        names = [contact.name for contact in contacts]
        return _response(
            "Saved contacts: " + "; ".join(names) + ".",
            data={"contacts": [{"id": contact.id, "name": contact.name} for contact in contacts]},
        )

    if intent == "open_trash":
        return _response(
            "Opening the trash.",
            client_action={"type": "open_trash"},
        )

    if intent == "open_settings":
        return _response(
            "Opening settings.",
            client_action={"type": "open_settings"},
        )

    if intent == "open_shares":
        return _response(
            "Opening your share links.",
            client_action={"type": "navigate", "url": "/shares"},
        )

    if intent == "open_dashboard":
        return _response(
            "Opening your dashboard.",
            client_action={"type": "navigate", "url": "/dashboard"},
        )

    if intent in {"ask_note", "error"}:
        return _response(plan["speak"])

    return _response(plan["speak"])


def _prepare_note(plan, ctx):
    title = plan.get("note_title") or "Voice note"
    content = plan.get("note_content") or ""
    if len(title) > 120 or len(content) > 2000:
        _audit("voice_create_note", "too_long", "FAILED")
        return _response("That note is too long for me to safely repeat back.")
    if not content.strip():
        _audit("voice_create_note", "missing_content", "FAILED")
        return _response(
            "What should the note say? Say something like: create a note titled "
            "Renewal dates with content passport expires in June."
        )
    summary = f"Create a note titled \"{title}\"."
    pending = _set_pending(ctx, {
        "kind": "create_note",
        "title": title,
        "content": content,
    }, summary)
    _audit("voice_create_note", "pending")
    return _response(
        f"I can create a note titled {title}. Say confirm or tap Confirm. "
        "I will not read the note content aloud.",
        pending=pending,
        data={"pending_summary": summary},
    )


def _prepare_share(plan, ctx):
    docs = _find_documents(plan.get("document_query") or "", ctx)
    if not docs:
        _audit("voice_share_prepare", "document_not_found")
        return _response(
            "I couldn't find that file. Say the file name, then try again."
        )
    if len(docs) > 1:
        _remember_documents(ctx, docs, "share_document")
        names = "; ".join(_list_line(doc) for doc in docs[:5])
        _audit("voice_share_prepare", "ambiguous_document")
        return _response(
            f"I found several matching files: {names}. Please say the exact file name."
        )
    doc = docs[0]
    contact_matches = _find_contacts(plan.get("contact_query") or "")
    if not contact_matches:
        _audit("voice_share_prepare", "contact_not_found")
        return _response(
            "I couldn't find that saved contact. Add the contact in Settings "
            "first; I never accept an email address by voice."
        )
    if len(contact_matches) > 1:
        names = "; ".join(contact.name for contact in contact_matches[:8])
        _audit("voice_share_prepare", "ambiguous_contact")
        return _response(
            f"I found several saved contacts: {names}. Please use a unique saved "
            "contact name, or rename one in Settings."
        )
    contact = contact_matches[0]
    expiry = plan.get("expiry") or "1h"
    usage = plan.get("usage") or "once"
    if expiry not in {"10m", "1h", "24h"} or usage not in {"once", "multi"}:
        _audit("voice_share_prepare", "invalid_options")
        return _response("I can share for 10 minutes, 1 hour, or 24 hours, one time or multiple times.")

    summary = (
        f"Share \"{doc.original_filename}\" with {contact.name} "
        f"({plan.get('usage_label') or usage}, {plan.get('expiry_label') or expiry}). "
        "No link password will be added."
    )
    pending = _set_pending(ctx, {
        "kind": "create_share",
        "document_id": doc.id,
        "contact_id": contact.id,
        "expiry": expiry,
        "usage": usage,
    }, summary)
    _remember_documents(ctx, docs, "share_document")
    _audit("voice_share_prepare", "pending")
    return _response(
        f"I can share {doc.original_filename} with {contact.name}, "
        f"{plan.get('usage_label') or usage}, for {plan.get('expiry_label') or expiry}. "
        "Say confirm or tap Confirm. No link password will be added; use the "
        "share dialog if you need a protected link.",
        pending=pending,
        data={"pending_summary": summary},
    )


def _execute_note(action):
    title = str(action.get("title") or "").strip()
    content = str(action.get("content") or "")
    if not title:
        _audit("voice_create_note", "missing_title", "FAILED")
        return _response("The pending note had no title, so nothing was created.", status="error")
    if len(title) > 120 or len(content) > 2000:
        _audit("voice_create_note", "too_long", "FAILED")
        return _response("The pending note was too long, so nothing was created.", status="error")
    if not content.strip():
        _audit("voice_create_note", "missing_content", "FAILED")
        return _response("The pending note was empty, so nothing was created.", status="error")
    note = Note(
        user_id=current_user.id,
        title=title[:255],
        content=encrypt_note(content.strip()),
        created_at=_now(),
    )
    db.session.add(note)
    db.session.flush()
    _audit("voice_create_note", "executed")
    db.session.commit()
    return _response(
        f"Note titled {title} was created. Refresh the page to see it.",
        status="ok",
    )


def _execute_share(action, share_creator):
    doc = Document.query.filter_by(
        id=action.get("document_id"),
        user_id=current_user.id,
        deleted_at=None,
    ).first()
    contact = Contact.query.filter_by(
        id=action.get("contact_id"),
        user_id=current_user.id,
    ).first()
    if not doc or not contact:
        _audit("voice_share", "revalidation_failed", "FAILED")
        db.session.commit()
        return _response(
            "That file or saved contact is no longer available, so nothing was shared.",
            status="error",
        )
    if not doc.file_path or not os.path.isfile(doc.file_path):
        _audit("voice_share", "file_missing", "FAILED")
        db.session.commit()
        return _response(
            "That file is missing on disk, so nothing was shared.",
            status="error",
        )
    try:
        result = share_creator(doc, contact, action.get("expiry"), action.get("usage"))
    except Exception:
        current_app.logger.exception("Voice share failed")
        _audit("voice_share", "failed", "FAILED")
        db.session.commit()
        return _response(
            "I couldn't create that share. Open the share dialog and try again.",
            status="error",
        )
    if not result or not result.get("ok"):
        message = (result or {}).get("message") or "The share could not be created."
        _audit("voice_share", "failed", "FAILED")
        db.session.commit()
        return _response(message, status="error")
    _audit("voice_share", "executed")
    db.session.commit()
    recipient = result.get("recipient") or contact.name
    if result.get("email_skipped"):
        email_note = "Voice never sends email; copy the link from this panel."
    elif result.get("email_sent"):
        email_note = "The email was sent."
    else:
        email_note = "Email was not sent, so copy the link from this panel."
    return _response(
        f"Share link created for {recipient}. The link is on screen now; copy it "
        f"before closing the assistant. {email_note}",
        f"Share link created for {recipient}. {email_note}\n"
        f"{result.get('url', '')}",
        data={
            "share_url": result.get("url"),
            "recipient": recipient,
            "email_sent": bool(result.get("email_sent")),
            "expires_in": result.get("expires_in"),
        },
        client_action={"type": "share_link", "url": result.get("url")},
    )


def _confirm_action(nonce, share_creator):
    action, error = _claim_pending(nonce)
    if error:
        _audit("voice_confirm", "rejected", "FAILED")
        db.session.commit()
        return _response(error, status="error")
    kind = (action or {}).get("kind")
    if kind == "create_note":
        return _execute_note(action)
    if kind == "create_share":
        return _execute_share(action, share_creator)
    _audit("voice_confirm", "unknown_action", "FAILED")
    db.session.commit()
    return _response("I didn't recognize the pending action, so nothing was changed.", status="error")


def _cancel_pending(nonce=None):
    ctx = VoiceContext.query.filter_by(user_id=current_user.id).first()
    if not ctx or not ctx.pending_nonce:
        return _response("There is no pending action to cancel.")
    if nonce and not secrets.compare_digest(str(ctx.pending_nonce), str(nonce)):
        return _response("That pending action is no longer available.", status="error")
    _clear_pending(ctx)
    _audit("voice_cancel", "cancelled")
    db.session.commit()
    return _response("Cancelled. Nothing was changed.")


def _get_request_json():
    if not request.is_json:
        return None, "Send JSON."
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, "Send a JSON object."
    return data, None


def create_voice_blueprint(share_creator):
    """Create the voice blueprint with a callback into the app's share layer."""
    bp = Blueprint("voice", __name__)

    @bp.after_request
    def _voice_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @bp.route("/voice/state", methods=["GET"])
    @login_required
    def voice_state():
        ctx = _get_context()
        pending = _pending_state(ctx)
        db.session.commit()
        return jsonify({
            "status": "ok",
            "language": VOICE_LANGUAGE,
            "pending": pending,
        })

    @bp.route("/voice/command", methods=["POST"])
    @login_required
    def voice_command():
        data, error = _get_request_json()
        if error:
            return jsonify({"status": "error", "message": error}), 400
        text = normalize_transcript(str(data.get("text") or ""))
        if not text:
            return jsonify({"status": "error", "message": "No speech text received."}), 400

        ctx = _get_context()
        if _rate_limited(ctx):
            _audit("voice_command", "rate_limited", "FAILED")
            db.session.commit()
            return jsonify(_response(
                "I'm getting too many requests. Please wait a minute and try again.",
                status="error",
            )), 429

        plan = parse_voice_command(text)
        intent = plan["intent"]

        if intent == "confirm":
            result = _confirm_action(data.get("nonce"), share_creator)
            db.session.commit()
            return jsonify(result)

        if intent == "cancel":
            return jsonify(_cancel_pending(data.get("nonce")))

        if intent == "blocked":
            _audit(plan.get("audit_tool") or "voice_blocked", "blocked", "FAILED")
            db.session.commit()
            return jsonify(_response(plan["speak"], status="blocked", intent=intent))

        if intent == "unknown":
            _audit("voice_unknown", "not_understood", "FAILED")
            db.session.commit()
            return jsonify(_response(plan["speak"], status="ok", intent=intent))

        if intent == "create_note":
            result = _prepare_note(plan, ctx)
            db.session.commit()
            return jsonify(result)

        if intent == "share_document":
            result = _prepare_share(plan, ctx)
            db.session.commit()
            return jsonify(result)

        # Read-only path. It never mutates vault data.
        result = _handle_read(plan, ctx)
        _audit(plan.get("audit_tool") or "voice_read", "ok")
        db.session.commit()
        result["intent"] = intent
        result["tier"] = plan.get("tier")
        return jsonify(result)

    @bp.route("/voice/confirm", methods=["POST"])
    @login_required
    def voice_confirm():
        data, error = _get_request_json()
        if error:
            return jsonify({"status": "error", "message": error}), 400
        ctx = _get_context()
        if _rate_limited(ctx):
            _audit("voice_confirm", "rate_limited", "FAILED")
            db.session.commit()
            return jsonify(_response(
                "I'm getting too many requests. Please wait a minute and try again.",
                status="error",
            )), 429
        result = _confirm_action(data.get("nonce"), share_creator)
        db.session.commit()
        return jsonify(result)

    @bp.route("/voice/cancel", methods=["POST"])
    @login_required
    def voice_cancel():
        data, error = _get_request_json()
        if error:
            return jsonify({"status": "error", "message": error}), 400
        ctx = _get_context()
        if _rate_limited(ctx):
            _audit("voice_cancel", "rate_limited", "FAILED")
            db.session.commit()
            return jsonify(_response(
                "I'm getting too many requests. Please wait a minute and try again.",
                status="error",
            )), 429
        return jsonify(_cancel_pending(data.get("nonce")))

    return bp
