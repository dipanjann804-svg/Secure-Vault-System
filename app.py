import os
import re
import json
import uuid
import gzip
import socket
import time
import hashlib
import secrets
import subprocess
import threading
import atexit
import tempfile
import zipfile
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
from flask import (
    Flask, render_template, url_for, request, redirect, flash, 
    send_from_directory, send_file, jsonify, abort, current_app,
    after_this_request, session
)
from flask_login import (
    LoginManager, login_user, login_required, current_user, logout_user
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text, func, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

from models import (
    db, User, Document, Note, ShareLink, ShareLinkItem, ShareAccess, Contact,
    VoiceContext, SecurityLog,
)
from encryption import encrypt_note, decrypt_note
from email_service import send_password_reset_email, send_share_email
from voice import create_voice_blueprint

login_manager = LoginManager()
csrf = CSRFProtect()

# Built-in folders shown by default. Users can create more by naming their own
# folder from the "Add files" tile on the dashboard.
ALLOWED_FOLDERS = ['Identity', 'Finance', 'Health', 'Legal']
FALLBACK_FOLDER = 'My Files'

# In-browser preview is restricted to these safe, non-executable types.
PREVIEW_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp'}
PREVIEW_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}

# Phrase the user has to type before their account is erased.
DELETE_ACCOUNT_PHRASE = 'DELETE MY ACCOUNT'

# --- Public share links ---
# expiry choice -> minutes
SHARE_EXPIRY_MINUTES = {'10m': 10, '1h': 60, '24h': 24 * 60}
# usage choice -> max downloads (None = unlimited)
SHARE_USAGE = {'once': 1, 'multi': None}
SHARE_KIND_DOCUMENT = 'document'
SHARE_KIND_FOLDER = 'folder'
# A folder bundle is built in memory/temp storage; cap it so one request cannot
# exhaust the host. Individual uploads are already capped at 50 MB.
SHARE_BUNDLE_MAX_BYTES = 200 * 1024 * 1024
SHARE_PASSWORD_MIN = 6
SHARE_PASSWORD_MAX = 128
# In-memory share-creation rate limiter (per user, per hour). This mirrors the
# app's existing login limiter; a multi-worker deployment should move it to a
# shared store later.
SHARE_CREATE_LIMIT = 20
SHARE_CREATE_WINDOW = 3600
_share_create_attempts = {}
# In-memory password gate limiter (per link + IP).
SHARE_PASSWORD_ATTEMPTS = 6
SHARE_PASSWORD_WINDOW = 600
_share_password_attempts = {}

# --- Login lockout policy (ported from time.py) ---
MAX_ATTEMPTS = 6
INITIAL_LOCKOUT = 30  # seconds
LOCKOUT_MULTIPLIER = 2
# In-memory tracker: key -> {"count": int, "lockout_until": float,
#                            "lockout_duration": int, "round_number": int}
_login_attempts = {}


def _lockout_key(login_id, ip_address):
    """Build a tracker key from IP + login id (mirrors time.py's per-password rounds)."""
    lid = (login_id or "").strip().lower() or "anonymous"
    return f"{ip_address or 'unknown'}:{lid}"


def _get_lockout_remaining(key):
    """Return seconds remaining in lockout, or 0 if not locked. Cleans up expired locks."""
    state = _login_attempts.get(key)
    if not state:
        return 0
    until = state.get("lockout_until", 0) or 0
    if not until:
        return 0  # no active lock — do NOT reset count here
    remaining = int(until - time.time())
    if remaining <= 0:
        # Lock expired -> start next round, reset attempt counter (time.py behaviour)
        state["count"] = 0
        state["lockout_until"] = 0
        return 0
    return remaining


def _record_failed_attempt(key):
    """Record a failed login. Returns (locked_now: bool, remaining: int, state: dict).

    Mirrors time.py exactly:
      Round 1: 6 fails -> lock 30s -> next 60s
      Round 2: 6 fails -> lock 60s -> next 120s
      Round N: lock INITIAL * (MULTIPLIER ** (N-1))
    """
    now = time.time()
    state = _login_attempts.setdefault(key, {
        "count": 0,
        "lockout_until": 0,
        "lockout_duration": INITIAL_LOCKOUT,
        "round_number": 1,
        "last_locked_for": 0,
        "last_locked_round": 0,
    })
    state["count"] += 1
    if state["count"] >= MAX_ATTEMPTS:
        locked_round = state["round_number"]  # the round that just finished (time.py)
        locked_for = state["lockout_duration"]  # time for THIS lock
        state["lockout_until"] = now + locked_for
        state["last_locked_for"] = locked_for
        state["last_locked_round"] = locked_round
        # Prepare next round with exponential backoff (time.py: lockout_duration *= 2)
        state["lockout_duration"] *= LOCKOUT_MULTIPLIER
        state["round_number"] += 1
        state["count"] = 0  # reset for next round after lock expires
        return True, locked_for, state
    return False, 0, state


def _lockout_display(key):
    """Build display values exactly matching time.py timing.

    Returns dict with: display_round, lockout_total, next_lockout, remaining.
    """
    state = _login_attempts.get(key, {})
    remaining = _get_lockout_remaining(key)
    # If currently locked, show the round that caused the lock
    display_round = state.get("last_locked_round") or max(1, state.get("round_number", 1) - (1 if remaining else 0))
    if remaining and not state.get("last_locked_round"):
        # Fallback for locks created before last_locked_* existed
        display_round = max(1, state.get("round_number", 2) - 1)
    lockout_total = state.get("last_locked_for") or INITIAL_LOCKOUT
    if remaining and not state.get("last_locked_for"):
        # lockout_duration was already doubled, so halve to get current total
        lockout_total = max(INITIAL_LOCKOUT, state.get("lockout_duration", INITIAL_LOCKOUT * 2) // LOCKOUT_MULTIPLIER)
    next_lockout = state.get("lockout_duration", INITIAL_LOCKOUT * LOCKOUT_MULTIPLIER if remaining else INITIAL_LOCKOUT)
    return {
        "state": state,
        "remaining": remaining,
        "display_round": display_round,
        "lockout_total": lockout_total,
        "next_lockout": next_lockout,
    }


def _reset_lockout(key):
    """Clear tracker on successful login (time.py: 'Access granted!' -> return)."""
    _login_attempts.pop(key, None)


def _clean_folder_name(value):
    """Normalize a user-supplied folder name (used by custom 'Add files' folders)."""
    name = re.sub(r"\s+", " ", (value or "").strip())
    name = name[:50].strip()
    return name or FALLBACK_FOLDER


def _get_hidden_folders(user):
    """Return the list of built-in folders the user has removed, safely parsed."""
    if not user or not user.hidden_folders:
        return []
    try:
        data = json.loads(user.hidden_folders)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(name) for name in data if isinstance(name, str)]


def _canonical_builtin_folder(name):
    """Return the built-in folder matching name (case-insensitive), else None."""
    for folder in ALLOWED_FOLDERS:
        if folder.lower() == (name or "").strip().lower():
            return folder
    return None


def _hash_share_token(token):
    """Hash a share token before it ever touches the database."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _lookup_share_link(token):
    """Find a share link by hashing the presented token. Returns None if unknown."""
    token = (token or "").strip()
    if not token or len(token) > 200:
        return None
    return ShareLink.query.filter_by(token=_hash_share_token(token)).first()


def _share_link_unavailable(link):
    """True when a link is revoked, expired, or used up its allowed downloads."""
    if link.revoked_at:
        return True
    if link.expires_at and link.expires_at <= datetime.utcnow():
        return True
    if link.max_uses is not None and (link.use_count or 0) >= link.max_uses:
        return True
    return False


def _share_kind(link):
    """Return 'folder' for a bundle link, otherwise 'document'."""
    if link.share_kind in (SHARE_KIND_DOCUMENT, SHARE_KIND_FOLDER):
        return link.share_kind
    return SHARE_KIND_FOLDER if link.folder and not link.document_id else SHARE_KIND_DOCUMENT


def _share_documents(link):
    """Resolve the live documents behind a link.

    Returns a list, or None when any expected document is missing or trashed.
    A folder bundle is frozen at creation time; if one member later disappears
    the whole link is treated as unavailable rather than silently shrinking.
    """
    if _share_kind(link) == SHARE_KIND_FOLDER:
        item_ids = [item.document_id for item in sorted(link.items, key=lambda i: i.id)]
        if not item_ids:
            return None
        docs = Document.query.filter(
            Document.id.in_(item_ids),
            Document.user_id == link.user_id,
            Document.deleted_at.is_(None),
        ).all()
        by_id = {doc.id: doc for doc in docs}
        if len(by_id) != len(item_ids):
            return None
        return [by_id[doc_id] for doc_id in item_ids if doc_id in by_id]

    if not link.document_id:
        return None
    doc = Document.query.filter_by(
        id=link.document_id,
        user_id=link.user_id,
        deleted_at=None,
    ).first()
    return [doc] if doc else None


def _share_label(link, docs=None):
    """Human-readable label for a share, used by the management page."""
    if _share_kind(link) == SHARE_KIND_FOLDER:
        return link.folder or "Shared folder"
    docs = docs if docs is not None else _share_documents(link)
    if docs:
        return docs[0].original_filename
    return "Shared document"


def _mask_ip(ip):
    """Mask an IP for display in share access history."""
    ip = (ip or "").strip()
    if not ip:
        return "—"
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:2]) + ":…"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return ip


def _valid_email(value):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (value or "").strip()))


def _share_create_rate_limited(user_id):
    """Allow a bounded number of share creations per user per hour."""
    now = time.time()
    attempts = [
        ts for ts in _share_create_attempts.get(user_id, [])
        if now - ts < SHARE_CREATE_WINDOW
    ]
    if len(attempts) >= SHARE_CREATE_LIMIT:
        _share_create_attempts[user_id] = attempts
        return True
    attempts.append(now)
    _share_create_attempts[user_id] = attempts
    return False


def _share_password_key(link):
    return f"{link.id}:{request.remote_addr or 'unknown'}"


def _share_password_blocked(link):
    """Brute-force guard for a protected link, per link + source IP."""
    now = time.time()
    key = _share_password_key(link)
    attempts = [
        ts for ts in _share_password_attempts.get(key, [])
        if now - ts < SHARE_PASSWORD_WINDOW
    ]
    if attempts:
        _share_password_attempts[key] = attempts
    else:
        _share_password_attempts.pop(key, None)
    return len(attempts) >= SHARE_PASSWORD_ATTEMPTS


def _record_share_password_failure(link):
    key = _share_password_key(link)
    _share_password_attempts.setdefault(key, []).append(time.time())


def _clear_share_password_failures(link):
    _share_password_attempts.pop(_share_password_key(link), None)


def _security_event(tool, outcome, status="SUCCESS", user_id=None, login_id=None):
    """Queue a SecurityLog row. The caller owns the transaction/commit."""
    db.session.add(SecurityLog(
        user_id=user_id,
        login_id=(login_id or "")[:150] or None,
        ip_address=(request.remote_addr or "")[:45] or None,
        status=status,
        user_agent=(request.user_agent.string or "")[:255] or None,
        tool=tool[:60],
        outcome=(outcome or "")[:30] or None,
    ))


def _record_share_access(link, outcome):
    """Persist one access-history row and a matching security event."""
    db.session.add(ShareAccess(
        share_id=link.id,
        ip_address=(request.remote_addr or "")[:45] or None,
        user_agent=(request.user_agent.string or "")[:255] or None,
        outcome=outcome,
    ))
    _security_event(
        "share_access",
        outcome,
        status="SUCCESS" if outcome in ("opened", "granted", "downloaded") else "FAILED",
        user_id=link.user_id,
        login_id=link.recipient_email or "public-share",
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _build_share_zip(docs):
    """Build a temporary ZIP for a frozen folder bundle.

    Returns the temp path. The caller must remove it after the response closes.
    """
    total = 0
    for doc in docs:
        if not doc.file_path or not os.path.isfile(doc.file_path):
            raise FileNotFoundError(doc.original_filename or doc.filename or "document")
        total += os.path.getsize(doc.file_path)
    if total > SHARE_BUNDLE_MAX_BYTES:
        raise ValueError("bundle-too-large")

    fd, path = tempfile.mkstemp(prefix="vault-share-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            used = set()
            for doc in docs:
                base = secure_filename(
                    doc.original_filename or doc.filename or f"document-{doc.id}"
                ) or f"document-{doc.id}"
                stem, ext = os.path.splitext(base)
                name = base
                n = 2
                while name.lower() in used:
                    name = f"{stem} ({n}){ext}"
                    n += 1
                used.add(name.lower())
                archive.write(doc.file_path, arcname=name)
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def _share_request_options(data, user_id):
    """Validate the JSON/form options shared by document and folder links.

    Returns (options, error). ``options`` contains a hashed password and a
    contact snapshot; raw passwords never touch the database or logs.
    """
    expiry = (data.get("expiry") or "").strip().lower()
    usage = (data.get("usage") or "").strip().lower()
    password = str(data.get("password") or "")
    contact_raw = str(data.get("contact_id") or "").strip()

    if expiry not in SHARE_EXPIRY_MINUTES:
        return None, "Choose an expiry time."
    if usage not in SHARE_USAGE:
        return None, "Choose how often the link can be used."
    if password and len(password) < SHARE_PASSWORD_MIN:
        return None, f"Link password must be at least {SHARE_PASSWORD_MIN} characters."
    if len(password) > SHARE_PASSWORD_MAX:
        return None, f"Link password must be at most {SHARE_PASSWORD_MAX} characters."

    contact = None
    if contact_raw:
        try:
            contact_id = int(contact_raw)
        except (TypeError, ValueError):
            return None, "Choose a saved contact."
        contact = Contact.query.filter_by(id=contact_id, user_id=user_id).first()
        if contact is None:
            return None, "That contact is not in your address book."

    return {
        "expiry": expiry,
        "usage": usage,
        "password_hash": generate_password_hash(password) if password else None,
        "contact": contact,
        "expires_at": datetime.utcnow() + timedelta(minutes=SHARE_EXPIRY_MINUTES[expiry]),
    }, None


def _share_time_left(expires_at):
    """Human readable time remaining, e.g. '10 minutes' or '24 hours'."""
    if not expires_at:
        return "soon"
    seconds = int((expires_at - datetime.utcnow()).total_seconds())
    if seconds <= 0:
        return "expired"
    # Round up so a fresh "10 minutes" link doesn't immediately read "9 minutes".
    minutes = max(1, (seconds + 59) // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def _format_file_size(size):
    """Small byte-size formatter used by the public share page."""
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return "0 B"
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"


# --- Trash (30-day recycle bin) ---
TRASH_RETENTION_DAYS = 30
TRASH_CHUNK_SIZE = 1024 * 1024
_last_trash_cleanup = 0.0


def _trash_days_left(deleted_at):
    """Whole days left before a trashed file is purged (at least 0)."""
    if not deleted_at:
        return TRASH_RETENTION_DAYS
    deadline = deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
    seconds = (deadline - datetime.utcnow()).total_seconds()
    if seconds <= 0:
        return 0
    return max(0, int((seconds + 86399) // 86400))


def _trash_items_for_user(user_id):
    """Serialise a user's trashed documents for the dashboard and Trash API."""
    docs = Document.query.filter(
        Document.user_id == user_id,
        Document.deleted_at.isnot(None),
    ).order_by(Document.deleted_at.desc()).all()

    items = []
    for doc in docs:
        original = doc.trash_original_size if doc.trash_original_size is not None else (doc.file_size or 0)
        compressed = doc.trash_size or 0
        if original > 0 and compressed > 0:
            saved_percent = max(0, round((1 - compressed / original) * 100))
        else:
            saved_percent = 0
        items.append({
            "id": doc.id,
            "name": doc.original_filename,
            "folder": doc.folder,
            "size": original,
            "size_label": _format_file_size(original),
            "trash_label": _format_file_size(compressed) if compressed else "—",
            "saved_percent": saved_percent,
            "deleted_at": doc.deleted_at.strftime("%b %d, %Y") if doc.deleted_at else "",
            "days_left": _trash_days_left(doc.deleted_at),
        })
    return items


def _compress_into_trash(doc):
    """Losslessly gzip a live document into the trash folder.

    Returns True when the file was successfully copied and removed from the
    live upload folder. The compressed stream is written beside its final name
    first and then moved into place, so a crash cannot leave a half-written
    recovery file under the real name.
    """
    if not doc.file_path or not os.path.isfile(doc.file_path):
        # Missing bytes: still move the record to trash so the user can see it
        # and the cleanup job can eventually drop it.
        doc.deleted_at = datetime.utcnow()
        doc.trash_path = None
        doc.trash_size = None
        doc.trash_original_size = doc.file_size
        doc.trash_checksum = None
        return True

    trash_name = f"{uuid.uuid4().hex}.gz"
    trash_path = os.path.join(current_app.config['TRASH_FOLDER'], trash_name)
    part_path = trash_path + ".part"
    digest = hashlib.sha256()
    original_size = 0

    try:
        with open(doc.file_path, "rb") as source, \
                gzip.open(part_path, "wb", compresslevel=9) as target:
            while True:
                chunk = source.read(TRASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                original_size += len(chunk)
                target.write(chunk)
        os.replace(part_path, trash_path)
    except (OSError, EOFError) as exc:
        print(f"[!] Could not compress {doc.original_filename} into trash: {exc}", flush=True)
        try:
            os.remove(part_path)
        except OSError:
            pass
        return False

    try:
        os.remove(doc.file_path)
    except OSError:
        # The compressed copy is safe; a leftover original is harmless.
        pass

    doc.deleted_at = datetime.utcnow()
    doc.trash_path = trash_path
    doc.trash_size = os.path.getsize(trash_path)
    doc.trash_original_size = original_size
    doc.trash_checksum = digest.hexdigest()
    doc.file_path = None
    return True


def _move_documents_to_trash(docs):
    """Move documents to the trash and retire their share links.

    Single-document links are deleted together with their access rows; folder
    bundles are revoked but kept so the owner still sees their access history.
    Either way, a link stops working the moment a file leaves the live folders.
    Returns how many documents moved.
    """
    moved = 0
    for doc in docs:
        if doc.deleted_at:
            continue
        if _compress_into_trash(doc):
            # Revoke/delete every link that points at this document. Folder
            # bundles are revoked (access history kept); single-document links
            # are deleted with their access rows by the ORM cascade.
            links = ShareLink.query.filter(
                (ShareLink.document_id == doc.id) |
                ShareLink.items.any(ShareLinkItem.document_id == doc.id)
            ).all()
            for link in links:
                if _share_kind(link) == SHARE_KIND_FOLDER:
                    link.revoked_at = datetime.utcnow()
                else:
                    db.session.delete(link)
            moved += 1
    return moved


def _restore_document_from_trash(doc):
    """Decompress a trashed document back to uploads, byte for byte.

    The gzip stream is verified against the original size and SHA-256 digest
    before the live file is accepted. A failed verification leaves the trash
    copy untouched so the user can retry.
    """
    if not doc.deleted_at or not doc.trash_path or not os.path.isfile(doc.trash_path):
        return False

    base_name = secure_filename(doc.original_filename or doc.filename or "document") or "document"
    live_name = f"{uuid.uuid4().hex}_{base_name}"
    live_path = os.path.join(current_app.config['UPLOAD_FOLDER'], live_name)
    digest = hashlib.sha256()
    restored_size = 0

    try:
        with gzip.open(doc.trash_path, "rb") as source, open(live_path, "wb") as target:
            while True:
                chunk = source.read(TRASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                restored_size += len(chunk)
                target.write(chunk)
    except (OSError, EOFError) as exc:
        print(f"[!] Could not restore {doc.original_filename} from trash: {exc}", flush=True)
        try:
            os.remove(live_path)
        except OSError:
            pass
        return False

    expected_size = doc.trash_original_size
    expected_checksum = doc.trash_checksum
    if (expected_checksum and digest.hexdigest() != expected_checksum) or \
            (expected_size is not None and restored_size != expected_size):
        print(f"[!] Trash restore verification failed for {doc.original_filename}", flush=True)
        try:
            os.remove(live_path)
        except OSError:
            pass
        return False

    try:
        os.remove(doc.trash_path)
    except OSError:
        pass

    doc.filename = live_name
    doc.file_path = live_path
    doc.file_size = restored_size
    doc.deleted_at = None
    doc.trash_path = None
    doc.trash_size = None
    doc.trash_original_size = None
    doc.trash_checksum = None
    return True


def _purge_trashed_document(doc):
    """Permanently remove a trashed document and its compressed bytes."""
    if doc.trash_path:
        try:
            os.remove(doc.trash_path)
        except OSError:
            pass
    db.session.delete(doc)


def _cleanup_expired_trash(force=False):
    """Permanently purge trash older than the retention window.

    Runs at most once an hour from before_request, and on demand with
    force=True (used at startup and by tests).
    """
    global _last_trash_cleanup
    now = time.time()
    if not force and now - _last_trash_cleanup < 3600:
        return 0
    _last_trash_cleanup = now

    cutoff = datetime.utcnow() - timedelta(days=TRASH_RETENTION_DAYS)
    expired = Document.query.filter(
        Document.deleted_at.isnot(None),
        Document.deleted_at <= cutoff,
    ).all()
    for doc in expired:
        _purge_trashed_document(doc)
    if expired:
        db.session.commit()
        print(f"[+] Purged {len(expired)} expired trash item(s)", flush=True)
    return len(expired)


def _sync_schema():
    """Add columns that were introduced in models.py after the database was created.

    db.create_all() only creates *missing tables*; it never alters an existing
    table. An older app.db (created before the profile fields were added to
    User) would therefore fail with "no such column: user.name". This adds any
    missing columns in place without touching existing rows.
    """
    inspector = sa_inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    preparer = db.engine.dialect.identifier_preparer

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            # A populated table can only gain a column that is nullable or has a default.
            if not column.nullable and column.default is None and column.server_default is None:
                print(
                    f"[!] Skipping {table.name}.{column.name}: NOT NULL with no default",
                    flush=True,
                )
                continue
            try:
                ddl = (
                    f"ALTER TABLE {preparer.quote(table.name)} "
                    f"ADD COLUMN {preparer.quote(column.name)} "
                    f"{column.type.compile(dialect=db.engine.dialect)}"
                )
                with db.engine.begin() as conn:
                    conn.execute(text(ddl))
                print(f"[+] Added missing column {table.name}.{column.name}", flush=True)
            except Exception as e:
                print(
                    f"[!] Could not add column {table.name}.{column.name}: {e}",
                    flush=True,
                )


def create_app():
    # Automatically locate templates whether uploaded inside templates/ or directly in root
    # (check both lower/upper case so it also works on case-sensitive Linux hosts)
    root_dir = os.path.dirname(os.path.abspath(__file__))
    tpl_dir = '.'
    for candidate in ('templates', 'Templates'):
        if os.path.isdir(os.path.join(root_dir, candidate)):
            tpl_dir = candidate
            break
    app = Flask(__name__, template_folder=tpl_dir)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-vault-123')
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True
    db_uri = (os.environ.get('DATABASE_URL') or '').strip().strip('"').strip("'")
    if db_uri.startswith("postgres://"):
        db_uri = db_uri.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri or "sqlite:///app.db"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if not app.config['SQLALCHEMY_DATABASE_URI'].startswith("sqlite:"):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }

    if os.environ.get("RENDER") and not db_uri:
        print("\n" + "=" * 70, flush=True)
        print("  [!] WARNING: RUNNING WITH SQLITE ON RENDER", flush=True)
        print("=" * 70, flush=True)
        print("  Render free instances use ephemeral disks.", flush=True)
        print("  User accounts will be erased whenever the instance spins down or redeploys!", flush=True)
        print("  To persist data permanently, attach a PostgreSQL database and set DATABASE_URL.", flush=True)
        print("=" * 70 + "\n", flush=True)
    
    # Uploads configuration. UPLOAD_FOLDER can point outside the app directory
    # (tests, ephemeral hosts) without changing the default behaviour.
    upload_folder = os.environ.get('UPLOAD_FOLDER') or os.path.join(app.root_path, 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = upload_folder
    # Trashed files are kept compressed here for 30 days before cleanup.
    # TRASH_FOLDER can point outside the app directory (tests, ephemeral hosts).
    trash_folder = os.environ.get('TRASH_FOLDER') or os.path.join(app.root_path, 'trash')
    os.makedirs(trash_folder, exist_ok=True)
    app.config['TRASH_FOLDER'] = trash_folder
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload
    # Used by the voice assistant to list built-in folders that are not hidden.
    app.config['VOICE_DEFAULT_FOLDERS'] = list(ALLOWED_FOLDERS)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    # The voice panel talks JSON. An expired session must not answer with an
    # HTML login redirect, so those paths get a 401 the panel can display.
    # Every other route keeps Flask-Login's default redirect behaviour.
    @login_manager.unauthorized_handler
    def _unauthorized():
        if request.path.startswith('/voice') or request.is_json:
            return jsonify({
                "status": "error",
                "message": "Your session expired. Refresh the page and sign in again.",
            }), 401
        flash("Please log in to access this page.", "error")
        return redirect(url_for(login_manager.login_view))

    # CSRF is required on every POST, including JSON fetches (X-CSRFToken).
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600
    csrf.init_app(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        if request.is_json or request.path.startswith('/voice'):
            return jsonify({
                "status": "error",
                "message": "Your session token is missing or expired. Refresh the page and try again.",
            }), 400
        flash("Your form session expired. Please try again.", "error")
        # Only redirect to a local path. Never trust Referer for a redirect.
        if request.path.startswith('/s/'):
            return redirect(request.path)
        return redirect(url_for("index"))

    @app.after_request
    def _share_security_headers(response):
        """Never let a public share response sit in a cache."""
        if request.path.startswith('/s/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    with app.app_context():
        try:
            db.create_all()
            _sync_schema()
            # Drop anything whose 30 days ran out while the app was offline.
            _cleanup_expired_trash(force=True)
        except Exception as e:
            print(f"[!] Warning during db.create_all: {e}")

    @app.before_request
    def _sweep_expired_trash():
        """Purge expired trash at most once an hour, on any request."""
        try:
            _cleanup_expired_trash()
        except Exception as e:
            print(f"[!] Trash cleanup skipped: {e}", flush=True)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # --- Health Route ---
    @app.route("/health/db")
    def health_db():
        try:
            db.session.execute(text("SELECT 1"))
            return {"db": "ok"}, 200
        except Exception as e:
            return {"db": "error", "detail": str(e)}, 500

    # --- Home ---
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    # --- Auth Routes ---
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        errors = []
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            email = (request.form.get("email") or request.form.get("registerEmail") or "").strip().lower()
            password = request.form.get("password") or request.form.get("registerPassword") or ""
            confirm_password = request.form.get("confirm_password") or request.form.get("confirmPassword") or ""

            if not (3 <= len(username) <= 80):
                errors.append("Username must be between 3 and 80 characters")

            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                errors.append("Please enter a valid email address")

            if len(password) < 6:
                errors.append("Password needs to be at least 6 characters")

            if password != confirm_password:
                errors.append("Passwords do not match")

            if not errors:
                try:
                    pw_hash = generate_password_hash(password)
                    user = User(
                        username=username,
                        email=email,
                        password_hash=pw_hash,
                        name=username
                    )
                    db.session.add(user)
                    db.session.commit()

                    flash("Account created successfully! Please log in.", "success")
                    return redirect(url_for('login'))
                except IntegrityError:
                    db.session.rollback()
                    errors.append("That username or email is already registered")

        return render_template("register.html", errors=errors)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        errors = []
        # Defaults for template (ported from time.py)
        lockout_remaining = 0
        attempts_remaining = MAX_ATTEMPTS
        # Pre-fill key from query or last typed value so GET can show lockout too
        preview_id = (request.form.get("email") if request.method == "POST" else request.args.get("email", "")) or ""
        preview_key = _lockout_key(preview_id, request.remote_addr)
        if preview_id:
            lockout_remaining = _get_lockout_remaining(preview_key)
            state = _login_attempts.get(preview_key)
            if state:
                attempts_remaining = max(0, MAX_ATTEMPTS - state.get("count", 0))

        if request.method == "POST":
            if request.is_json:
                data = request.get_json(silent=True) or {}
                login_id = (data.get("email") or "").strip()
                password = data.get("password") or ""
            else:
                login_id = (request.form.get("email") or "").strip()
                password = request.form.get("password") or ""
            key = _lockout_key(login_id, request.remote_addr)

            # 1) Check active lockout first (time.py: "Too many failed attempts...")
            disp = _lockout_display(key)
            lockout_remaining = disp["remaining"]
            if lockout_remaining > 0:
                if request.is_json:
                    return jsonify({
                        "status": "locked",
                        "message": f"Too many failed attempts. Try again in {lockout_remaining} seconds.",
                        "lockout_remaining": lockout_remaining
                    }), 429
                errors.append(
                    f"Too many failed attempts. Try again in {lockout_remaining} seconds."
                )
                return render_template(
                    "login.html",
                    errors=errors,
                    lockout_remaining=lockout_remaining,
                    lockout_total=disp["lockout_total"],
                    next_lockout=disp["next_lockout"],
                    attempts_remaining=0,
                    max_attempts=MAX_ATTEMPTS,
                    round_number=disp["display_round"],
                )

            if not login_id:
                errors.append("Email or Username is required")
            if not password:
                errors.append("Password is required")

            if not errors:
                user = User.query.filter(
                    (func.lower(User.email) == login_id.lower()) |
                    (func.lower(User.username) == login_id.lower())
                ).first()
                if user and check_password_hash(user.password_hash, password):
                    _reset_lockout(key)  # time.py: "Access granted!" -> return
                    login_user(user)

                    # Log successful login
                    try:
                        log = SecurityLog(
                            user_id=user.id,
                            login_id=login_id,
                            ip_address=request.remote_addr,
                            status="SUCCESS",
                            user_agent=request.user_agent.string
                        )
                        db.session.add(log)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

                    if request.is_json:
                        return jsonify({
                            "status": "ok",
                            "username": user.username,
                            "name": user.name or user.username,
                            "redirect": url_for("dashboard")
                        })
                    flash("Logged in successfully!", "success")
                    return redirect(url_for("dashboard"))
                else:
                    # 2) Wrong password -> count attempt (time.py inner loop)
                    locked_now, locked_for, state = _record_failed_attempt(key)
                    try:
                        log = SecurityLog(
                            user_id=user.id if user else None,
                            login_id=login_id,
                            ip_address=request.remote_addr,
                            status="FAILED",
                            user_agent=request.user_agent.string
                        )
                        db.session.add(log)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

                    if locked_now:
                        lockout_remaining = locked_for
                        disp2 = _lockout_display(key)
                        errors.append(
                            f"Too many failed attempts. Locked for {locked_for} seconds."
                        )
                    else:
                        attempts_remaining = max(0, MAX_ATTEMPTS - state.get("count", 0))
                        errors.append(
                            f"Invalid email or password. Attempts remaining: {attempts_remaining}"
                        )
                        lockout_remaining = 0
                        disp2 = {"lockout_total": locked_for or INITIAL_LOCKOUT,
                                 "next_lockout": state.get("lockout_duration", INITIAL_LOCKOUT),
                                 "display_round": state.get("round_number", 1)}

                    if request.is_json:
                        return jsonify({
                            "status": "error",
                            "message": errors[-1] if errors else "Invalid email or password",
                            "lockout_remaining": lockout_remaining
                        }), 401

                    return render_template(
                        "login.html",
                        errors=errors,
                        lockout_remaining=lockout_remaining,
                        lockout_total=disp2["lockout_total"] if locked_now else INITIAL_LOCKOUT,
                        next_lockout=disp2["next_lockout"],
                        attempts_remaining=attempts_remaining if not locked_now else 0,
                        max_attempts=MAX_ATTEMPTS,
                        round_number=disp2["display_round"] if locked_now else state.get("round_number", 1),
                    )

        if preview_id and lockout_remaining > 0:
            disp0 = _lockout_display(preview_key)
            return render_template(
                "login.html",
                errors=errors,
                lockout_remaining=lockout_remaining,
                lockout_total=disp0["lockout_total"],
                next_lockout=disp0["next_lockout"],
                attempts_remaining=0,
                max_attempts=MAX_ATTEMPTS,
                round_number=disp0["display_round"],
            )
        state = _login_attempts.get(preview_key, {}) if preview_id else {}
        return render_template(
            "login.html",
            errors=errors,
            lockout_remaining=0,
            lockout_total=INITIAL_LOCKOUT,
            next_lockout=state.get("lockout_duration", INITIAL_LOCKOUT) if state else INITIAL_LOCKOUT,
            attempts_remaining=attempts_remaining,
            max_attempts=MAX_ATTEMPTS,
            round_number=state.get("round_number", 1) if state else 1,
        )

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out", "success")
        return redirect(url_for("index"))

    def get_serializer():
        return URLSafeTimedSerializer(app.config['SECRET_KEY'])

    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = socket.gethostbyname(socket.gethostname())
        finally:
            s.close()
        return ip or '127.0.0.1'

    def get_base_url():
        if os.environ.get('RENDER_EXTERNAL_URL'):
            return os.environ.get('RENDER_EXTERNAL_URL').strip().rstrip('/')

        configured_url = os.environ.get('APP_URL') or os.environ.get('BASE_URL')
        if configured_url and not (os.environ.get('RENDER') and ('192.168.' in configured_url or '10.' in configured_url or 'localhost' in configured_url or '127.0.0.1' in configured_url)):
            return configured_url.strip().rstrip('/')

        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        host = request.headers.get('X-Forwarded-Host', request.host)
        if ('127.0.0.1' in host or 'localhost' in host) and not os.environ.get('RENDER'):
            local_ip = get_local_ip()
            port = host.split(':')[-1] if ':' in host else '5555'
            return f"{scheme}://{local_ip}:{port}"

        return f"{scheme}://{host}"

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        errors = []
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                errors.append("Please enter a valid email address")
            else:
                user = User.query.filter(func.lower(User.email) == email).first()
                if user:
                    print(f"[+] Password reset requested for registered user: {user.email}")
                    s = get_serializer()
                    token = s.dumps(user.email, salt="password-reset-salt")
                    base_url = get_base_url()
                    reset_link = f"{base_url}/reset-password/{token}"
                    print(f"\n[+] PASSWORD RESET LINK for {user.email}: {reset_link}\n", flush=True)

                    result = send_password_reset_email(user.email, reset_link)
                    if result:
                        flash(
                            "A password reset link has been sent to your email inbox! (Please check your Spam or Junk folder if you do not see it).",
                            "success"
                        )
                        return redirect(url_for("login"))
                    else:
                        print(f"[!] Email dispatch failed for {user.email}: {result.message}")
                        return render_template(
                            "forgot_password.html",
                            errors=errors,
                            reset_url=reset_link,
                            delivery_failed=True,
                            delivery_error=result.message,
                            submitted_email=user.email,
                        )
                else:
                    print(f"[!] Password reset requested but email NOT found in database: {email}. Note: If using SQLite on Render Free tier, restarting/sleeping the instance wipes local databases.")
                    flash(
                        "If an account exists with that email address, a password reset link has been sent to your email inbox.",
                        "success"
                    )
                    return redirect(url_for("login"))

        return render_template("forgot_password.html", errors=errors)

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        s = get_serializer()
        try:
            email = s.loads(token, salt="password-reset-salt", max_age=3600)
        except (SignatureExpired, BadTimeSignature):
            flash("The password reset link is invalid or has expired.", "error")
            return redirect(url_for("forgot_password"))

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("User account not found.", "error")
            return redirect(url_for("forgot_password"))

        errors = []
        if request.method == "POST":
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if len(new_password) < 6:
                errors.append("Password needs to be at least 6 characters")
            if new_password != confirm_password:
                errors.append("Passwords do not match")

            if not errors:
                user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash("Password has been reset successfully! You can now log in.", "success")
                return redirect(url_for("login"))

        return render_template("reset_password.html", errors=errors)

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        errors = []
        if request.method == "POST":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if not check_password_hash(current_user.password_hash, current_password):
                errors.append("Current password is incorrect")

            if len(new_password) < 6:
                errors.append("New password needs to be at least 6 characters")

            if new_password != confirm_password:
                errors.append("New passwords do not match")

            if not errors:
                current_user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash("Password updated successfully!", "success")
                return redirect(url_for("dashboard"))

        return render_template("change_password.html", errors=errors)

    # --- Dashboard ---
    def _dashboard_context(active_folder=None):
        """Build the data the dashboard / folder page templates need."""
        # Fetch user's live documents (trashed files live in the Trash bin).
        documents = Document.query.filter(
            Document.user_id == current_user.id,
            Document.deleted_at.is_(None),
        ).order_by(Document.created_at.desc()).all()

        # Fetch the user's trashed documents with their compressed sizes.
        trash = _trash_items_for_user(current_user.id)

        # Fetch user's notes and decrypt contents
        raw_notes = Note.query.filter_by(user_id=current_user.id).order_by(Note.created_at.desc()).all()
        notes = []
        for n in raw_notes:
            notes.append({
                "id": n.id,
                "title": n.title or "Untitled Note",
                "content": decrypt_note(n.content),
                "created_at": n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else ""
            })

        contacts = Contact.query.filter_by(user_id=current_user.id).order_by(
            Contact.name.asc()
        ).all()

        # Build the folder list: built-in folders plus any custom folders the user
        # created through "Add files" (inferred from their uploaded documents).
        custom_folders = sorted({
            doc.folder for doc in documents
            if doc.folder and doc.folder not in ALLOWED_FOLDERS
        })
        all_folders = ALLOWED_FOLDERS + custom_folders

        # Built-in folders the user removed with the "×" on the folder card
        hidden_folders = _get_hidden_folders(current_user)
        folders = [f for f in all_folders if f not in hidden_folders]

        # Calculate counts per folder
        folder_counts = {folder: 0 for folder in folders}
        for doc in documents:
            if doc.folder in folder_counts:
                folder_counts[doc.folder] += 1

        return {
            "user": current_user,
            "documents": documents,
            "notes": notes,
            "trash": trash,
            "contacts": contacts,
            "folders": folders,
            "default_folders": [f for f in ALLOWED_FOLDERS if f not in hidden_folders],
            "hidden_folders": [f for f in ALLOWED_FOLDERS if f in hidden_folders],
            "folder_counts": folder_counts,
            "active_folder": active_folder,
            "folder_view": active_folder is not None,
        }

    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html", **_dashboard_context())

    @app.route("/folders/<path:folder_name>")
    @login_required
    def folder_page(folder_name):
        """Dedicated page showing one folder and the files inside it."""
        context = _dashboard_context()
        wanted = (folder_name or "").strip()

        # Match case-insensitively so /folders/finance and /folders/Finance both work.
        match = next(
            (name for name in context["folders"] if name.lower() == wanted.lower()),
            None
        )
        if match is None:
            abort(404)

        context["active_folder"] = match
        context["folder_view"] = True
        return render_template("dashboard.html", **context)

    # --- Document Routes ---
    @app.route("/documents/upload", methods=["POST"])
    @login_required
    def upload_document():
        folder = _clean_folder_name(request.form.get("folder"))

        files = request.files.getlist("files") or request.files.getlist("file")
        if not files or all(f.filename == '' for f in files):
            flash("No file selected", "error")
            return redirect(url_for("dashboard"))

        uploaded_count = 0
        for file in files:
            if file and file.filename:
                orig_name = secure_filename(file.filename) or "document"
                unique_name = f"{uuid.uuid4().hex}_{orig_name}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
                file.save(save_path)

                file_size = os.path.getsize(save_path)
                file_type = file.content_type or "application/octet-stream"

                doc = Document(
                    user_id=current_user.id,
                    filename=unique_name,
                    original_filename=file.filename,
                    file_path=save_path,
                    file_size=file_size,
                    file_type=file_type,
                    folder=folder
                )
                db.session.add(doc)
                uploaded_count += 1

        db.session.commit()
        flash(f"Successfully uploaded {uploaded_count} file(s) to '{folder}'!", "success")
        # Uploads started from a folder page return to that folder's page.
        if (request.form.get("return_to") or "").strip():
            return redirect(url_for("folder_page", folder_name=folder))
        return redirect(url_for("dashboard"))

    @app.route("/documents/<int:doc_id>/download")
    @login_required
    def download_document(doc_id):
        # Trashed files can never be downloaded; restore them first.
        doc = Document.query.filter_by(
            id=doc_id, user_id=current_user.id, deleted_at=None
        ).first_or_404()
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            doc.filename,
            as_attachment=True,
            download_name=doc.original_filename
        )

    @app.route("/documents/<int:doc_id>/view")
    @login_required
    def preview_document(doc_id):
        """Owner-only in-browser preview for safe file types (no download)."""
        doc = Document.query.filter_by(
            id=doc_id, user_id=current_user.id, deleted_at=None
        ).first_or_404()

        ext = os.path.splitext(doc.original_filename or doc.filename or "")[1].lower()
        if ext not in PREVIEW_EXTENSIONS:
            abort(415)
        if not doc.file_path or not os.path.isfile(doc.file_path):
            abort(404)

        mimetype = PREVIEW_MIME_TYPES.get(ext)
        if not mimetype:
            abort(415)

        response = send_file(
            doc.file_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=doc.original_filename or "file",
        )
        safe_name = secure_filename(doc.original_filename or "") or "file"
        response.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "private, no-store"
        if ext == ".pdf":
            # Same-origin framing only; the PDF viewer runs without page scripts.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
            )
        else:
            # Images are inert; sandbox them and forbid every external resource.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
            )
        return response

    # --- Public share links ---
    def _create_share_record(docs, kind, folder, options, send_email=True):
        """Create a share row, freeze a folder bundle, and optionally email a contact."""
        token = secrets.token_urlsafe(32)  # 256 bits; only the hash is stored
        contact = options["contact"]
        link = ShareLink(
            user_id=current_user.id,
            document_id=docs[0].id if kind == SHARE_KIND_DOCUMENT else None,
            token=_hash_share_token(token),
            max_uses=SHARE_USAGE[options["usage"]],
            use_count=0,
            expires_at=options["expires_at"],
            password_hash=options["password_hash"],
            share_kind=kind,
            folder=folder,
            recipient_contact_id=contact.id if contact else None,
            recipient_name=contact.name if contact else None,
            recipient_email=contact.email if contact else None,
        )
        db.session.add(link)
        if kind == SHARE_KIND_FOLDER:
            for doc in docs:
                db.session.add(ShareLinkItem(share=link, document_id=doc.id))

        _security_event(
            "share_create",
            kind,
            user_id=current_user.id,
            login_id=(contact.email if contact else current_user.email),
        )
        db.session.commit()

        share_url = url_for("shared_file", token=token, _external=True)
        email_sent = False
        email_message = ""
        if contact and send_email:
            result = send_share_email(
                to_email=contact.email,
                recipient_name=contact.name,
                owner_name=current_user.name or current_user.username,
                share_url=share_url,
                expires_in=_share_time_left(link.expires_at),
                item_label=_share_label(link, docs),
                item_count=len(docs),
                is_folder=(kind == SHARE_KIND_FOLDER),
                has_password=bool(link.password_hash),
            )
            email_sent = bool(result)
            email_message = result.message
            if not email_sent:
                print(f"[!] Share email failed for {contact.email}: {email_message}", flush=True)
        return link, token, email_sent, email_message

    def _voice_share_for_user(doc, contact, expiry, usage):
        """Revalidate and create one voice-confirmed document share.

        The voice layer supplies IDs only. This callback is the final authority:
        it re-checks ownership, live-file state, saved-contact ownership, and
        the allowed expiry/usage values before using the normal share path.
        """
        if _share_create_rate_limited(current_user.id):
            return {
                "ok": False,
                "message": "Too many share links created. Try again later.",
            }
        doc = Document.query.filter_by(
            id=doc.id, user_id=current_user.id, deleted_at=None
        ).first()
        if not doc or not doc.file_path or not os.path.isfile(doc.file_path):
            return {
                "ok": False,
                "message": "That file is no longer available. Nothing was shared.",
            }
        contact = Contact.query.filter_by(
            id=contact.id, user_id=current_user.id
        ).first()
        if not contact:
            return {
                "ok": False,
                "message": "That saved contact is no longer available. Nothing was shared.",
            }

        options, error = _share_request_options({
            "expiry": expiry,
            "usage": usage,
            "password": "",
            "contact_id": str(contact.id),
        }, current_user.id)
        if error:
            return {"ok": False, "message": error}

        try:
            link, token, email_sent, email_message = _create_share_record(
                [doc], SHARE_KIND_DOCUMENT, None, options, send_email=False
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Voice share creation failed")
            return {
                "ok": False,
                "message": "The share could not be created. Please try the share dialog.",
            }

        return {
            "ok": True,
            "url": url_for("shared_file", token=token, _external=True),
            "recipient": contact.name,
            "email_sent": email_sent,
            "email_skipped": True,
            "email_message": email_message,
            "expires_in": _share_time_left(link.expires_at),
            "usage": options["usage"],
        }

    app.register_blueprint(create_voice_blueprint(_voice_share_for_user))

    @app.route("/documents/<int:doc_id>/share", methods=["POST"])
    @login_required
    def create_share_link(doc_id):
        # Only live files can be shared; trashed files must be restored first.
        doc = Document.query.filter_by(
            id=doc_id, user_id=current_user.id, deleted_at=None
        ).first_or_404()
        if not doc.file_path or not os.path.isfile(doc.file_path):
            return jsonify({"status": "error", "message": "That file is missing on disk."}), 404
        if _share_create_rate_limited(current_user.id):
            return jsonify({
                "status": "error",
                "message": "Too many share links created. Try again later.",
            }), 429

        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        options, error = _share_request_options(data, current_user.id)
        if error:
            return jsonify({"status": "error", "message": error}), 400

        link, token, email_sent, email_message = _create_share_record(
            [doc], SHARE_KIND_DOCUMENT, None, options
        )
        return jsonify({
            "status": "ok",
            "url": url_for("shared_file", token=token, _external=True),
            "expires_in": _share_time_left(link.expires_at),
            "usage": options["usage"],
            "kind": SHARE_KIND_DOCUMENT,
            "file_count": 1,
            "recipient": link.recipient_name,
            "email_sent": email_sent,
            "email_message": email_message,
            "has_password": bool(link.password_hash),
        })

    @app.route("/folders/share", methods=["POST"])
    @login_required
    def create_folder_share():
        """Share a frozen snapshot of every live file in one folder."""
        if _share_create_rate_limited(current_user.id):
            return jsonify({
                "status": "error",
                "message": "Too many share links created. Try again later.",
            }), 429

        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        folder = _clean_folder_name(data.get("name"))
        docs = Document.query.filter(
            Document.user_id == current_user.id,
            Document.deleted_at.is_(None),
            func.lower(Document.folder) == folder.lower(),
        ).order_by(Document.created_at.asc()).all()
        if not docs:
            return jsonify({
                "status": "error",
                "message": "That folder has no live files to share.",
            }), 404
        for doc in docs:
            if not doc.file_path or not os.path.isfile(doc.file_path):
                return jsonify({
                    "status": "error",
                    "message": f"'{doc.original_filename}' is missing on disk.",
                }), 404

        options, error = _share_request_options(data, current_user.id)
        if error:
            return jsonify({"status": "error", "message": error}), 400

        link, token, email_sent, email_message = _create_share_record(
            docs, SHARE_KIND_FOLDER, docs[0].folder, options
        )
        return jsonify({
            "status": "ok",
            "url": url_for("shared_file", token=token, _external=True),
            "expires_in": _share_time_left(link.expires_at),
            "usage": options["usage"],
            "kind": SHARE_KIND_FOLDER,
            "file_count": len(docs),
            "recipient": link.recipient_name,
            "email_sent": email_sent,
            "email_message": email_message,
            "has_password": bool(link.password_hash),
        })

    @app.route("/shares")
    @login_required
    def list_shares():
        """Owner view of created links and their access history."""
        links = ShareLink.query.filter_by(user_id=current_user.id).order_by(
            ShareLink.created_at.desc()
        ).all()
        rows = []
        for link in links:
            docs = _share_documents(link)
            accesses = sorted(
                link.accesses, key=lambda item: item.timestamp or datetime.min, reverse=True
            )[:25]
            rows.append({
                "id": link.id,
                "kind": _share_kind(link),
                "label": _share_label(link, docs),
                "recipient_name": link.recipient_name,
                "recipient_email": link.recipient_email,
                "created_at": link.created_at.strftime("%b %d, %Y %H:%M") if link.created_at else "",
                "expires_at": link.expires_at.strftime("%b %d, %Y %H:%M") if link.expires_at else "",
                "expires_in": _share_time_left(link.expires_at),
                "active": not _share_link_unavailable(link),
                "revoked": bool(link.revoked_at),
                "revoked_at": link.revoked_at.strftime("%b %d, %Y %H:%M") if link.revoked_at else "",
                "used": link.use_count or 0,
                "max_uses": link.max_uses,
                "has_password": bool(link.password_hash),
                "file_count": len(docs) if docs else 0,
                "missing": docs is None,
                "accesses": [{
                    "outcome": access.outcome,
                    "timestamp": access.timestamp.strftime("%b %d, %H:%M") if access.timestamp else "",
                    "ip": _mask_ip(access.ip_address),
                    "user_agent": (access.user_agent or "")[:80],
                } for access in accesses],
            })
        return render_template("shares.html", share_rows=rows)

    @app.route("/shares/<int:share_id>/revoke", methods=["POST"])
    @login_required
    def revoke_share_link(share_id):
        link = ShareLink.query.filter_by(
            id=share_id, user_id=current_user.id
        ).first_or_404()
        if link.revoked_at:
            flash("That link was already revoked.", "error")
            return redirect(url_for("list_shares"))
        link.revoked_at = datetime.utcnow()
        _security_event(
            "share_revoke",
            "revoked",
            user_id=current_user.id,
            login_id=link.recipient_email or current_user.email,
        )
        db.session.commit()
        flash("Share link revoked. It stops working immediately.", "success")
        return redirect(url_for("list_shares"))

    @app.route("/contacts", methods=["GET", "POST"])
    @login_required
    def contacts():
        """Saved recipients. Voice can only select these, never dictate an email."""
        if request.method == "GET":
            rows = Contact.query.filter_by(user_id=current_user.id).order_by(
                Contact.name.asc()
            ).all()
            return jsonify({
                "status": "ok",
                "contacts": [
                    {"id": row.id, "name": row.name, "email": row.email}
                    for row in rows
                ],
            })

        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        name = (data.get("name") or "").strip()[:150]
        email = (data.get("email") or "").strip().lower()[:150]
        if not name:
            return jsonify({"status": "error", "message": "Contact name is required."}), 400
        if not _valid_email(email):
            return jsonify({"status": "error", "message": "Enter a valid contact email."}), 400
        existing = Contact.query.filter(
            Contact.user_id == current_user.id,
            func.lower(Contact.email) == email,
        ).first()
        if existing:
            return jsonify({"status": "error", "message": "That contact already exists."}), 409

        contact = Contact(user_id=current_user.id, name=name, email=email)
        db.session.add(contact)
        try:
            db.session.flush()
            _security_event(
                "contact_add", "created",
                user_id=current_user.id, login_id=email,
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"status": "error", "message": "That contact already exists."}), 409
        return jsonify({
            "status": "ok",
            "contact": {"id": contact.id, "name": contact.name, "email": contact.email},
        })

    @app.route("/s/<token>", methods=["GET", "POST"])
    def shared_file(token):
        """Public landing page, password gate, and access history entry point."""
        link = _lookup_share_link(token)
        if link is None:
            abort(404)
        if _share_link_unavailable(link):
            outcome = "revoked" if link.revoked_at else (
                "exhausted" if link.max_uses is not None and
                (link.use_count or 0) >= link.max_uses else "expired"
            )
            _record_share_access(link, outcome)
            return render_template("share_unavailable.html"), 410

        docs = _share_documents(link)
        if docs is None:
            _record_share_access(link, "denied")
            return render_template("share_unavailable.html"), 410

        protected = bool(link.password_hash)
        access_key = f"share_ok_{link.id}"
        granted = (not protected) or bool(session.get(access_key))
        password_error = None

        if request.method == "POST":
            if not protected:
                return redirect(url_for("shared_file", token=token))
            if _share_password_blocked(link):
                _record_share_access(link, "password_blocked")
                return render_template(
                    "share.html",
                    token=token,
                    needs_password=True,
                    password_error="Too many attempts. Try again later.",
                    protected=True,
                ), 429
            supplied = request.form.get("password") or ""
            if check_password_hash(link.password_hash, supplied):
                _clear_share_password_failures(link)
                session[access_key] = True
                _record_share_access(link, "granted")
                return redirect(url_for("shared_file", token=token))
            _record_share_password_failure(link)
            _record_share_access(link, "password_failed")
            granted = False
            password_error = "That password is not correct."

        if protected and not granted:
            return render_template(
                "share.html",
                token=token,
                needs_password=True,
                password_error=password_error,
                protected=True,
            ), (401 if password_error else 200)

        if request.method == "GET":
            _record_share_access(link, "opened")

        total_size = sum((doc.file_size or 0) for doc in docs)
        return render_template(
            "share.html",
            token=token,
            needs_password=False,
            protected=protected,
            share_kind=_share_kind(link),
            docs=docs,
            label=_share_label(link, docs),
            size_label=_format_file_size(total_size),
            file_count=len(docs),
            expires_in=_share_time_left(link.expires_at),
            single_use=(link.max_uses == 1),
        )

    @app.route("/s/<token>/download", methods=["POST"])
    def shared_file_download(token):
        """Consume one download atomically, then stream a file or frozen ZIP."""
        link = _lookup_share_link(token)
        if link is None:
            abort(404)
        if _share_link_unavailable(link):
            outcome = "revoked" if link.revoked_at else (
                "exhausted" if link.max_uses is not None and
                (link.use_count or 0) >= link.max_uses else "expired"
            )
            _record_share_access(link, outcome)
            return render_template("share_unavailable.html"), 410

        docs = _share_documents(link)
        if docs is None:
            _record_share_access(link, "denied")
            return render_template("share_unavailable.html"), 410

        if link.password_hash and not session.get(f"share_ok_{link.id}"):
            _record_share_access(link, "denied")
            return redirect(url_for("shared_file", token=token))

        zip_path = None
        if _share_kind(link) == SHARE_KIND_FOLDER:
            try:
                zip_path = _build_share_zip(docs)
            except FileNotFoundError:
                _record_share_access(link, "denied")
                return render_template("share_unavailable.html"), 410
            except ValueError:
                _record_share_access(link, "denied")
                return jsonify({
                    "status": "error",
                    "message": "This bundle is too large to download at once.",
                }), 413

        # Claim one use atomically so two simultaneous requests cannot both succeed.
        query = ShareLink.query.filter(
            ShareLink.id == link.id,
            ShareLink.revoked_at.is_(None),
        )
        if link.expires_at:
            query = query.filter(ShareLink.expires_at > datetime.utcnow())
        if link.max_uses is not None:
            query = query.filter(func.coalesce(ShareLink.use_count, 0) < link.max_uses)
        claimed = query.update(
            {ShareLink.use_count: func.coalesce(ShareLink.use_count, 0) + 1},
            synchronize_session=False,
        )
        db.session.commit()

        if claimed != 1:
            if zip_path:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            _record_share_access(link, "denied")
            return render_template("share_unavailable.html"), 410

        _record_share_access(link, "downloaded")

        if zip_path:
            @after_this_request
            def _remove_bundle(response):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
                return response

            folder_name = secure_filename(link.folder or "shared-folder") or "shared-folder"
            response = send_file(
                zip_path,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{folder_name}-secure-vault.zip",
            )
        else:
            doc = docs[0]
            response = send_file(
                doc.file_path,
                mimetype="application/octet-stream",
                as_attachment=True,
                download_name=doc.original_filename or "shared-file",
            )

        # Shared files are never rendered inside the vault's origin.
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    @app.route("/documents/delete", methods=["POST"])
    @login_required
    def delete_documents():
        if request.is_json:
            data = request.get_json() or {}
            doc_ids = data.get("ids", [])
        else:
            doc_ids = request.form.getlist("ids") or request.form.getlist("doc_ids")
            if not doc_ids and request.form.get("id"):
                doc_ids = [request.form.get("id")]

        if not doc_ids:
            if request.is_json:
                return jsonify({"status": "error", "message": "No document IDs provided"}), 400
            flash("No documents selected for deletion", "error")
            return redirect(url_for("dashboard"))

        moved_count = 0
        for doc_id in doc_ids:
            try:
                doc = Document.query.filter_by(
                    id=int(doc_id), user_id=current_user.id, deleted_at=None
                ).first()
                if doc and _move_documents_to_trash([doc]):
                    moved_count += 1
            except (ValueError, TypeError):
                continue

        db.session.commit()

        if request.is_json:
            return jsonify({
                "status": "ok",
                "deleted_count": moved_count,
                "moved_to_trash": moved_count,
                "retention_days": TRASH_RETENTION_DAYS,
            })

        flash(
            f"Moved {moved_count} document(s) to Trash — permanently deleted after "
            f"{TRASH_RETENTION_DAYS} days",
            "success",
        )
        return redirect(url_for("dashboard"))

    # --- Trash Routes ---
    @app.route("/trash/list")
    @login_required
    def list_trash():
        """Return the signed-in user's trash as JSON (used by the Trash modal)."""
        return jsonify({
            "status": "ok",
            "retention_days": TRASH_RETENTION_DAYS,
            "items": _trash_items_for_user(current_user.id),
        })

    @app.route("/trash/restore", methods=["POST"])
    @login_required
    def restore_trash():
        """Restore trashed documents, decompressing them back byte for byte."""
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        doc_ids = data.get("ids") or ([data.get("id")] if data.get("id") else [])

        if not doc_ids:
            return jsonify({"status": "error", "message": "No document IDs provided"}), 400

        restored_count = 0
        failed = []
        for doc_id in doc_ids:
            try:
                doc = Document.query.filter_by(
                    id=int(doc_id), user_id=current_user.id
                ).filter(Document.deleted_at.isnot(None)).first()
            except (ValueError, TypeError):
                continue
            if doc is None:
                continue
            if _restore_document_from_trash(doc):
                restored_count += 1
            else:
                failed.append(doc.original_filename)

        db.session.commit()

        message = f"Restored {restored_count} file(s)."
        if failed:
            message += " Could not restore: " + ", ".join(failed)
        return jsonify({
            "status": "ok" if restored_count else "error",
            "restored_count": restored_count,
            "failed": failed,
            "message": message,
        })

    @app.route("/trash/purge", methods=["POST"])
    @login_required
    def purge_trash():
        """Permanently delete trashed documents now (cannot be undone)."""
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        doc_ids = data.get("ids") or ([data.get("id")] if data.get("id") else [])

        if not doc_ids:
            return jsonify({"status": "error", "message": "No document IDs provided"}), 400

        purged_count = 0
        for doc_id in doc_ids:
            try:
                doc = Document.query.filter_by(
                    id=int(doc_id), user_id=current_user.id
                ).filter(Document.deleted_at.isnot(None)).first()
            except (ValueError, TypeError):
                continue
            if doc is not None:
                _purge_trashed_document(doc)
                purged_count += 1

        db.session.commit()
        return jsonify({"status": "ok", "purged_count": purged_count})

    @app.route("/trash/empty", methods=["POST"])
    @login_required
    def empty_trash():
        """Permanently delete every trashed document for this user."""
        docs = Document.query.filter(
            Document.user_id == current_user.id,
            Document.deleted_at.isnot(None),
        ).all()
        for doc in docs:
            _purge_trashed_document(doc)
        db.session.commit()
        return jsonify({"status": "ok", "purged_count": len(docs)})

    # --- Folder Routes ---
    @app.route("/folders/remove", methods=["POST"])
    @login_required
    def remove_folder():
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        folder = _clean_folder_name(data.get("name"))

        # Move every live file in this folder to the Trash (restorable for 30 days)
        docs = Document.query.filter(
            Document.user_id == current_user.id,
            Document.deleted_at.is_(None),
            func.lower(Document.folder) == folder.lower(),
        ).all()
        deleted_count = _move_documents_to_trash(docs)

        # Built-in folders are recreated on every dashboard load, so remember
        # that the user removed this one. Custom folders disappear on their own.
        canonical = _canonical_builtin_folder(folder)
        if canonical:
            hidden = _get_hidden_folders(current_user)
            if canonical not in hidden:
                hidden.append(canonical)
                current_user.hidden_folders = json.dumps(hidden)

        db.session.commit()

        if request.is_json:
            return jsonify({
                "status": "ok",
                "folder": canonical or folder,
                "deleted_count": deleted_count,
                "moved_to_trash": deleted_count,
            })

        flash(
            f"Removed folder '{canonical or folder}' — {deleted_count} file(s) moved to Trash",
            "success",
        )
        return redirect(url_for("dashboard"))

    @app.route("/folders/restore", methods=["POST"])
    @login_required
    def restore_folder():
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        folder = _clean_folder_name(data.get("name"))
        canonical = _canonical_builtin_folder(folder) or folder

        hidden = [
            name for name in _get_hidden_folders(current_user)
            if name.lower() != canonical.lower()
        ]
        current_user.hidden_folders = json.dumps(hidden)
        db.session.commit()

        if request.is_json:
            return jsonify({"status": "ok", "folder": canonical})

        flash(f"Restored folder '{canonical}'", "success")
        return redirect(url_for("dashboard"))

    # --- Notepad / Notes Routes ---
    @app.route("/notes/add", methods=["POST"])
    @login_required
    def add_note():
        if request.is_json:
            data = request.get_json() or {}
            title = data.get("title", "")
            content = data.get("content", "")
        else:
            title = request.form.get("title", "")
            content = request.form.get("content", "")

        if not content or not str(content).strip():
            if request.is_json:
                return jsonify({"status": "error", "message": "Note content cannot be empty"}), 400
            flash("Note content cannot be empty", "error")
            return redirect(url_for("dashboard"))

        title_str = str(title).strip() or "Quick Note"

        encrypted_content = encrypt_note(str(content).strip())
        note = Note(
            user_id=current_user.id,
            title=title_str,
            content=encrypted_content
        )
        db.session.add(note)
        db.session.commit()

        if request.is_json:
            return jsonify({
                "status": "ok",
                "note": {
                    "id": note.id,
                    "title": note.title,
                    "content": content,
                    "created_at": note.created_at.strftime("%Y-%m-%d %H:%M")
                }
            })

        flash("Note saved and encrypted successfully!", "success")
        return redirect(url_for("dashboard"))

    @app.route("/notes/delete", methods=["POST"])
    @login_required
    def delete_notes():
        if request.is_json:
            data = request.get_json() or {}
            note_ids = data.get("ids", [])
        else:
            note_ids = request.form.getlist("ids") or request.form.getlist("note_ids")
            if not note_ids and request.form.get("id"):
                note_ids = [request.form.get("id")]

        if not note_ids:
            if request.is_json:
                return jsonify({"status": "error", "message": "No note IDs provided"}), 400
            flash("No notes selected for deletion", "error")
            return redirect(url_for("dashboard"))

        deleted_count = 0
        for note_id in note_ids:
            try:
                note = Note.query.filter_by(id=int(note_id), user_id=current_user.id).first()
                if note:
                    db.session.delete(note)
                    deleted_count += 1
            except (ValueError, TypeError):
                continue

        db.session.commit()

        if request.is_json:
            return jsonify({"status": "ok", "deleted_count": deleted_count})

        flash(f"Deleted {deleted_count} note(s)", "success")
        return redirect(url_for("dashboard"))

    # --- Profile Routes ---
    @app.route("/profile/update", methods=["POST"])
    @login_required
    def update_profile():
        name = request.form.get("name")
        phone = request.form.get("phone")
        date_of_birth = request.form.get("date_of_birth")
        gender = request.form.get("gender")
        location = request.form.get("location")
        profile_photo = request.form.get("profile_photo")  # base64 string or image URL

        # Handle uploaded avatar file if provided
        if "avatar_file" in request.files:
            avatar_file = request.files["avatar_file"]
            if avatar_file and avatar_file.filename:
                ext = secure_filename(avatar_file.filename).split('.')[-1]
                avatar_name = f"avatar_{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
                avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar_name)
                avatar_file.save(avatar_path)
                profile_photo = f"/uploads/{avatar_name}"

        if name is not None:
            current_user.name = name.strip()
        if phone is not None:
            current_user.phone = phone.strip()
        if date_of_birth is not None:
            current_user.date_of_birth = date_of_birth.strip()
        if gender is not None:
            current_user.gender = gender.strip()
        if location is not None:
            current_user.location = location.strip()
        if profile_photo:
            current_user.profile_photo = profile_photo

        current_user.updated_at = datetime.utcnow()
        db.session.commit()

        if request.is_json:
            return jsonify({"status": "ok", "message": "Profile updated successfully"})

        flash("Profile updated successfully!", "success")
        return redirect(url_for("dashboard"))

    @app.route("/account/delete", methods=["POST"])
    @login_required
    def delete_account():
        """Permanently erase the signed-in user's account and everything it owns."""
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        password = data.get("password") or ""
        phrase = (data.get("phrase") or "").strip()

        def fail(message):
            if request.is_json:
                return jsonify({"status": "error", "message": message}), 400
            flash(message, "error")
            return redirect(url_for("dashboard"))

        if not current_user.check_password(password):
            return fail("Current password is incorrect.")
        if phrase.upper() != DELETE_ACCOUNT_PHRASE:
            return fail(f'Type "{DELETE_ACCOUNT_PHRASE}" to confirm deletion.')

        user = current_user._get_current_object()
        user_id = user.id
        upload_folder = app.config['UPLOAD_FOLDER']

        # Fixed deletion order: share links -> documents/files -> notes -> avatar -> account.
        # 1) Share links (and their items/access rows; bulk deletes bypass ORM cascade)
        share_ids = [
            row.id for row in ShareLink.query.filter_by(user_id=user_id).all()
        ]
        if share_ids:
            ShareAccess.query.filter(ShareAccess.share_id.in_(share_ids)).delete(
                synchronize_session=False
            )
            ShareLinkItem.query.filter(ShareLinkItem.share_id.in_(share_ids)).delete(
                synchronize_session=False
            )
            ShareLink.query.filter(ShareLink.id.in_(share_ids)).delete(
                synchronize_session=False
            )
        Contact.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        VoiceContext.query.filter_by(user_id=user_id).delete(synchronize_session=False)

        # 2) Documents and the files they point at (live uploads and trash copies)
        for doc in Document.query.filter_by(user_id=user_id).all():
            for path in (doc.file_path, doc.trash_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            db.session.delete(doc)
        db.session.flush()

        # 3) Notes
        for note in Note.query.filter_by(user_id=user_id).all():
            db.session.delete(note)
        db.session.flush()

        # 4) Avatar
        photo = user.profile_photo or ""
        if photo.startswith("/uploads/"):
            avatar_path = os.path.join(upload_folder, os.path.basename(photo))
            if os.path.exists(avatar_path):
                try:
                    os.remove(avatar_path)
                except OSError:
                    pass
        user.profile_photo = None

        # Security logs are kept for auditing, but the identity is masked.
        SecurityLog.query.filter_by(user_id=user_id).update(
            {SecurityLog.user_id: None, SecurityLog.login_id: "deleted-account"},
            synchronize_session=False,
        )

        # 5) The account itself
        db.session.delete(user)
        db.session.commit()

        logout_user()
        flash("Your account has been permanently deleted.", "success")

        if request.is_json:
            return jsonify({"status": "ok"})
        return redirect(url_for("login"))

    @app.route('/uploads/<filename>')
    @login_required
    def uploaded_file(filename):
        # Only the owner's own avatar is reachable here — never other uploads.
        if not filename.startswith(f"avatar_{current_user.id}_"):
            abort(404)
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    return app


def get_local_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    finally:
        s.close()
    return ip or '127.0.0.1'


app = create_app()


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port(port):
    if not _port_in_use(port):
        return True

    current_pid = os.getpid()
    pids_to_kill = set()

    try:
        out = subprocess.check_output('netstat -ano -p tcp', shell=True, text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[0].upper() == 'TCP' and parts[3].upper() == 'LISTENING':
                if parts[1].endswith(f':{port}'):
                    try:
                        p = int(parts[4])
                        if p > 0 and p != current_pid:
                            pids_to_kill.add(p)
                    except ValueError:
                        pass
    except Exception:
        pass

    if not pids_to_kill:
        try:
            ps_cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess\""
            out = subprocess.check_output(ps_cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    p = int(line)
                    if p > 0 and p != current_pid:
                        pids_to_kill.add(p)
        except Exception:
            pass

    for pid in pids_to_kill:
        print(f"  [*] Port {port} is occupied by stale process (PID {pid}). Terminating it...", flush=True)
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                           capture_output=True, check=False)
        except Exception:
            pass

    for _ in range(6):
        time.sleep(0.3)
        if not _port_in_use(port):
            return True

    return not _port_in_use(port)


def _ensure_available_port(desired_port):
    if not _port_in_use(desired_port):
        return desired_port

    print(f"  [!] Port {desired_port} is already in use. Attempting to free it...", flush=True)
    if _free_port(desired_port):
        print(f"  [+] Successfully freed port {desired_port}.", flush=True)
        return desired_port

    curr_port = desired_port + 1
    while _port_in_use(curr_port):
        curr_port += 1

    print(f"  [i] Port {desired_port} could not be freed. Switched to next available port: {curr_port}", flush=True)
    return curr_port


if __name__ == "__main__":
    _raw_port = (os.environ.get("PORT") or "").strip()
    port = int(_raw_port) if _raw_port.isdigit() else 5555
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        port = _ensure_available_port(port)
        os.environ['PORT'] = str(port)

    local_ip = get_local_ip_address()

    print("\n" + "=" * 70, flush=True)
    print("  [+] SECURE VAULT SYSTEM IS RUNNING", flush=True)
    print("=" * 70, flush=True)
    print(f"  * Local PC Access:    http://127.0.0.1:{port}", flush=True)
    print(f"  * Local Wi-Fi Access: http://{local_ip}:{port}", flush=True)
    print("=" * 70 + "\n", flush=True)

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=True)
