"""Minimal Flask runner that bypasses SQLAlchemy DLL blockage.
Runs the Secure Vault System using only Flask's built-in server.
This avoids the SQLAlchemy _collections_cy DLL import issue."""
import os
import sys

# Avoid SQLAlchemy import entirely by creating Flask app from scratch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file, jsonify, abort
from flask_login import LoginManager, login_user, login_required, current_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import sqlite3
import json
import uuid
import re
import socket
import subprocess
import time
import gzip
import hashlib
import secrets
from datetime import datetime, timedelta

load_dotenv()

# Bump this whenever the runner changes so you can confirm which code is live
# by opening http://127.0.0.1:5555/health/db
BUILD_TAG = "minimal-4 (share links + 30-day trash + account deletion)"

# Create Flask app (no SQLAlchemy import needed for basic operation)
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# Secret key loaded from environment
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-vault-123')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Uploads directory (matches original app.py behaviour)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_UPLOAD_DIR = os.path.join(_BASE_DIR, 'uploads')
os.makedirs(_UPLOAD_DIR, exist_ok=True)
app.config['UPLOAD_FOLDER'] = _UPLOAD_DIR

# Compressed trash storage (30-day recycle bin). Override with TRASH_FOLDER.
_TRASH_DIR = os.environ.get('TRASH_FOLDER') or os.path.join(_BASE_DIR, 'trash')
os.makedirs(_TRASH_DIR, exist_ok=True)
app.config['TRASH_FOLDER'] = _TRASH_DIR
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Use SQLite directly without SQLAlchemy
import os as os_mod

BASE_DIR = os_mod.path.dirname(os_mod.path.abspath(__file__))
DB_PATH = os_mod.path.join(BASE_DIR, 'app.db')

# Simple database functions (bare minimum)
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Create tables that the app needs
    cur.execute('''CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password_hash TEXT,
        name TEXT,
        phone TEXT,
        date_of_birth TEXT,
        gender TEXT,
        location TEXT,
        profile_photo TEXT,
        hidden_folders TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    user_cols = {row[1] for row in cur.execute("PRAGMA table_info(user)").fetchall()}
    for col in ("phone", "date_of_birth", "gender", "location",
                "profile_photo", "hidden_folders"):
        if col not in user_cols:
            try:
                cur.execute(f"ALTER TABLE user ADD COLUMN {col} TEXT")
            except Exception:
                pass
    cur.execute('''CREATE TABLE IF NOT EXISTS note (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        title TEXT,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES user (id)
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS document (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        filename TEXT,
        original_filename TEXT,
        file_path TEXT,
        file_size INTEGER,
        file_type TEXT,
        folder TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES user (id)
    )''')
    # Lightweight migration: add columns introduced after an older app.db was made
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(document)").fetchall()}
    for col, ddl in (
        ("file_type", "ALTER TABLE document ADD COLUMN file_type TEXT"),
        ("file_path", "ALTER TABLE document ADD COLUMN file_path TEXT"),
        ("folder", "ALTER TABLE document ADD COLUMN folder TEXT"),
        ("deleted_at", "ALTER TABLE document ADD COLUMN deleted_at TIMESTAMP"),
        ("trash_path", "ALTER TABLE document ADD COLUMN trash_path TEXT"),
        ("trash_size", "ALTER TABLE document ADD COLUMN trash_size INTEGER"),
        ("trash_original_size", "ALTER TABLE document ADD COLUMN trash_original_size INTEGER"),
        ("trash_checksum", "ALTER TABLE document ADD COLUMN trash_checksum TEXT"),
    ):
        if col not in existing_cols:
            try:
                cur.execute(ddl)
            except Exception:
                pass

    # Public share links. Only a SHA-256 hash of the token is ever stored.
    cur.execute('''CREATE TABLE IF NOT EXISTS share_link (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        document_id INTEGER,
        token TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        max_uses INTEGER,
        use_count INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES user (id)
    )''')
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_share_link_token ON share_link (token)"
    )
    conn.commit()
    conn.close()

init_db()

# Basic login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class _MinimalUser:
    """Minimal User class for flask_login + template compatibility."""
    def __init__(self, row):
        self.id = row['id']
        self.username = row['username']
        self.email = row['email']
        self.password_hash = row['password_hash']
        self.name = row['name']
        # Profile fields read by dashboard.html (default to None when absent)
        self.phone = row['phone'] if 'phone' in row.keys() else None
        self.date_of_birth = row['date_of_birth'] if 'date_of_birth' in row.keys() else None
        self.gender = row['gender'] if 'gender' in row.keys() else None
        self.location = row['location'] if 'location' in row.keys() else None
        self.profile_photo = row['profile_photo'] if 'profile_photo' in row.keys() else None
        self.hidden_folders = row['hidden_folders'] if 'hidden_folders' in row.keys() else None
    
    def is_authenticated(self):
        return True
    
    def is_active(self):
        return True
    
    def is_anonymous(self):
        return False
    
    def full_name(self):
        return self.name
    
    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM user WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    if row:
        return _MinimalUser(row)
    return None

# Basic routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json(silent=True) or {}
            email = data.get('email', '')
            password = data.get('password', '')
        else:
            email = request.form.get('email', '')
            password = request.form.get('password', '')

        conn = get_db()
        email_clean = (email or '').strip()
        row = conn.execute(
            "SELECT * FROM user WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (email_clean.lower(), email_clean.lower())
        ).fetchone()
        conn.close()

        if row and check_password_hash(row['password_hash'], password):
            user = _MinimalUser(row)
            login_user(user)
            if request.is_json:
                return jsonify({
                    "status": "ok",
                    "username": user.username,
                    "name": user.name or user.username,
                    "redirect": url_for('dashboard')
                })
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            if request.is_json:
                return jsonify({
                    "status": "error",
                    "message": "Invalid email or password"
                }), 401
            flash('Invalid email or password')
    return render_template('login.html')

ALLOWED_FOLDERS = ['Identity', 'Finance', 'Health', 'Legal']

# In-browser preview is restricted to safe, non-executable types.
PREVIEW_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp'}
PREVIEW_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}

# --- Public share links ---
# expiry choice -> minutes
SHARE_EXPIRY_MINUTES = {'10m': 10, '1h': 60, '24h': 24 * 60}
# usage choice -> max downloads (None = unlimited)
SHARE_USAGE = {'once': 1, 'multi': None}

# --- Trash (30-day recycle bin) ---
TRASH_RETENTION_DAYS = 30
TRASH_CHUNK_SIZE = 1024 * 1024
_last_trash_cleanup = 0.0

# Phrase required before an account is erased.
DELETE_ACCOUNT_PHRASE = 'DELETE MY ACCOUNT'


def _resolve_doc_file(doc):
    """Return (directory, filename) for a document row, or None if the file is gone.

    Handles both absolute paths (new uploads) and stale/relative paths (old rows)
    by falling back to the configured uploads folder + stored filename.
    """
    file_path = doc['file_path'] if 'file_path' in doc.keys() else None
    if file_path and os_mod.path.isfile(file_path):
        return os_mod.path.dirname(file_path), os_mod.path.basename(file_path)
    filename = doc['filename'] if 'filename' in doc.keys() else None
    if filename:
        candidate = os_mod.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os_mod.path.isfile(candidate):
            return app.config['UPLOAD_FOLDER'], filename
    return None


def _parse_dt(value):
    """Parse a SQLite timestamp string into a datetime (or None)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def _format_file_size(size):
    """Small byte-size formatter used by the Trash modal and share page."""
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


# --- Share link helpers ---
def _hash_share_token(token):
    """Hash a share token before it ever touches the database."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _lookup_share_link(token):
    """Find a share link by hashing the presented token. Returns None if unknown."""
    token = (token or "").strip()
    if not token or len(token) > 200:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM share_link WHERE token = ?", (_hash_share_token(token),)
    ).fetchone()
    conn.close()
    return row


def _share_link_unavailable(link):
    """True when a link has expired or used up its allowed downloads."""
    expires_at = _parse_dt(link['expires_at'])
    if expires_at and expires_at <= datetime.utcnow():
        return True
    max_uses = link['max_uses']
    if max_uses is not None and (link['use_count'] or 0) >= max_uses:
        return True
    return False


def _share_time_left(expires_at):
    """Human readable time remaining, e.g. '10 minutes' or '24 hours'."""
    expires_at = _parse_dt(expires_at)
    if not expires_at:
        return "soon"
    seconds = int((expires_at - datetime.utcnow()).total_seconds())
    if seconds <= 0:
        return "expired"
    minutes = max(1, (seconds + 59) // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


# --- Trash helpers ---
def _trash_days_left(deleted_at):
    """Whole days left before a trashed file is purged (at least 0)."""
    deleted_at = _parse_dt(deleted_at)
    if not deleted_at:
        return TRASH_RETENTION_DAYS
    deadline = deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
    seconds = (deadline - datetime.utcnow()).total_seconds()
    if seconds <= 0:
        return 0
    return max(0, int((seconds + 86399) // 86400))


def _trash_items_for_user(user_id):
    """Serialise a user's trashed documents for the dashboard and Trash API."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM document WHERE user_id = ? AND deleted_at IS NOT NULL "
        "ORDER BY deleted_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    items = []
    for doc in rows:
        original = doc['trash_original_size'] if doc['trash_original_size'] is not None else (doc['file_size'] or 0)
        compressed = doc['trash_size'] or 0
        if original > 0 and compressed > 0:
            saved_percent = max(0, round((1 - compressed / original) * 100))
        else:
            saved_percent = 0
        deleted_at = _parse_dt(doc['deleted_at'])
        items.append({
            "id": doc['id'],
            "name": doc['original_filename'],
            "folder": doc['folder'],
            "size": original,
            "size_label": _format_file_size(original),
            "trash_label": _format_file_size(compressed) if compressed else "—",
            "saved_percent": saved_percent,
            "deleted_at": deleted_at.strftime("%b %d, %Y") if deleted_at else "",
            "days_left": _trash_days_left(doc['deleted_at']),
        })
    return items


def _compress_into_trash(conn, doc):
    """Losslessly gzip a live document into the trash folder.

    Returns True when the file was successfully copied and removed from the
    live upload folder. The compressed stream is written beside its final name
    first and then moved into place, so a crash cannot leave a half-written
    recovery file under the real name. Share links are revoked in the same
    transaction, so they stop working immediately.
    """
    resolved = _resolve_doc_file(doc)
    if not resolved:
        # Missing bytes: still move the record to trash so the user can see it
        # and the cleanup job can eventually drop it.
        conn.execute(
            "UPDATE document SET deleted_at = ?, trash_path = NULL, trash_size = NULL, "
            "trash_original_size = ?, trash_checksum = NULL, file_path = NULL "
            "WHERE id = ?",
            (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"), doc['file_size'], doc['id']),
        )
        conn.execute("DELETE FROM share_link WHERE document_id = ?", (doc['id'],))
        return True

    directory, filename = resolved
    source_path = os_mod.path.join(directory, filename)
    trash_name = f"{uuid.uuid4().hex}.gz"
    trash_path = os_mod.path.join(app.config['TRASH_FOLDER'], trash_name)
    part_path = trash_path + ".part"
    digest = hashlib.sha256()
    original_size = 0

    try:
        with open(source_path, "rb") as source, \
                gzip.open(part_path, "wb", compresslevel=9) as target:
            while True:
                chunk = source.read(TRASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                original_size += len(chunk)
                target.write(chunk)
        os_mod.replace(part_path, trash_path)
    except (OSError, EOFError) as exc:
        print(f"[!] Could not compress {doc['original_filename']} into trash: {exc}", flush=True)
        try:
            os_mod.remove(part_path)
        except OSError:
            pass
        return False

    stored_path = doc['file_path'] if 'file_path' in doc.keys() else None
    unremoved = None
    for path in {source_path, stored_path}:
        if not path or not os_mod.path.isfile(path):
            continue
        for _attempt in range(3):
            try:
                os_mod.remove(path)
                break
            except OSError:
                time.sleep(0.1)
        else:
            # Another process may still be reading it (e.g. an in-flight
            # download). Keep the path so purge/restore can retry the delete.
            unremoved = path

    conn.execute(
        "UPDATE document SET deleted_at = ?, trash_path = ?, trash_size = ?, "
        "trash_original_size = ?, trash_checksum = ?, file_path = ? WHERE id = ?",
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
         trash_path, os_mod.path.getsize(trash_path),
         original_size, digest.hexdigest(), unremoved, doc['id']),
    )
    conn.execute("DELETE FROM share_link WHERE document_id = ?", (doc['id'],))
    return True


def _restore_document_from_trash(conn, doc):
    """Decompress a trashed document back to uploads, byte for byte.

    The gzip stream is verified against the original size and SHA-256 digest
    before the live file is accepted. A failed verification leaves the trash
    copy untouched so the user can retry.
    """
    trash_path = doc['trash_path']
    if not trash_path or not os_mod.path.isfile(trash_path):
        return False

    base_name = secure_filename(doc['original_filename'] or doc['filename'] or "document") or "document"
    live_name = f"{uuid.uuid4().hex}_{base_name}"
    live_path = os_mod.path.join(app.config['UPLOAD_FOLDER'], live_name)
    digest = hashlib.sha256()
    restored_size = 0

    try:
        with gzip.open(trash_path, "rb") as source, open(live_path, "wb") as target:
            while True:
                chunk = source.read(TRASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                restored_size += len(chunk)
                target.write(chunk)
    except (OSError, EOFError) as exc:
        print(f"[!] Could not restore {doc['original_filename']} from trash: {exc}", flush=True)
        try:
            os_mod.remove(live_path)
        except OSError:
            pass
        return False

    expected_size = doc['trash_original_size']
    expected_checksum = doc['trash_checksum']
    if (expected_checksum and digest.hexdigest() != expected_checksum) or \
            (expected_size is not None and restored_size != expected_size):
        print(f"[!] Trash restore verification failed for {doc['original_filename']}", flush=True)
        try:
            os_mod.remove(live_path)
        except OSError:
            pass
        return False

    try:
        os_mod.remove(trash_path)
    except OSError:
        pass

    # If the original could not be removed when it was trashed, drop it now.
    stale_path = doc['file_path']
    if stale_path and stale_path != live_path and os_mod.path.isfile(stale_path):
        try:
            os_mod.remove(stale_path)
        except OSError:
            pass

    conn.execute(
        "UPDATE document SET filename = ?, file_path = ?, file_size = ?, "
        "deleted_at = NULL, trash_path = NULL, trash_size = NULL, "
        "trash_original_size = NULL, trash_checksum = NULL WHERE id = ?",
        (live_name, live_path, restored_size, doc['id']),
    )
    return True


def _purge_trashed_document(conn, doc):
    """Permanently remove a trashed document and its compressed bytes."""
    for path in (doc['trash_path'], doc['file_path']):
        if path and os_mod.path.isfile(path):
            try:
                os_mod.remove(path)
            except OSError:
                pass
    conn.execute("DELETE FROM share_link WHERE document_id = ?", (doc['id'],))
    conn.execute("DELETE FROM document WHERE id = ?", (doc['id'],))


def _cleanup_expired_trash(force=False):
    """Permanently purge trash older than the retention window."""
    global _last_trash_cleanup
    now = time.time()
    if not force and now - _last_trash_cleanup < 3600:
        return 0
    _last_trash_cleanup = now

    cutoff = (datetime.utcnow() - timedelta(days=TRASH_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM document WHERE deleted_at IS NOT NULL AND deleted_at <= ?",
        (cutoff,),
    ).fetchall()
    for doc in rows:
        _purge_trashed_document(conn, doc)
    conn.commit()
    conn.close()
    if rows:
        print(f"[+] Purged {len(rows)} expired trash item(s)", flush=True)
    return len(rows)


@app.before_request
def _maybe_cleanup_trash():
    """Run the 30-day purge at most once an hour, without delaying requests."""
    try:
        _cleanup_expired_trash()
    except Exception:
        pass


# Purge anything that expired while the app was stopped.
try:
    _cleanup_expired_trash(force=True)
except Exception as _exc:
    print(f"[!] Trash cleanup skipped: {_exc}", flush=True)


class _Doc:
    """Attribute-access wrapper so dashboard.html can use doc.id, doc.created_at, etc."""
    def __init__(self, row):
        self.id = row['id']
        self.filename = row['filename'] if 'filename' in row.keys() else ''
        self.original_filename = row['original_filename'] if 'original_filename' in row.keys() else ''
        self.file_size = row['file_size'] if 'file_size' in row.keys() else 0
        self.file_type = row['file_type'] if 'file_type' in row.keys() else ''
        self.folder = row['folder'] if 'folder' in row.keys() else ''
        self.created_at = _parse_dt(row['created_at'] if 'created_at' in row.keys() else None)


def _dashboard_context(active_folder=None):
    """Build the full set of variables dashboard.html expects.

    Mirrors _dashboard_context() from the original app.py so the template
    never receives a Jinja2 Undefined value (which cannot be JSON-serialized).
    """
    conn = get_db()
    doc_rows = conn.execute(
        "SELECT * FROM document WHERE user_id = ? AND deleted_at IS NULL ORDER BY id DESC",
        (current_user.id,),
    ).fetchall()
    note_rows = conn.execute(
        "SELECT * FROM note WHERE user_id = ? ORDER BY id DESC", (current_user.id,)
    ).fetchall()
    conn.close()

    documents = [_Doc(r) for r in doc_rows]

    notes = []
    for r in note_rows:
        created = r['created_at'] if 'created_at' in r.keys() else None
        dt = _parse_dt(created)
        notes.append({
            "id": r['id'],
            "title": (r['title'] if 'title' in r.keys() else None) or "Untitled Note",
            "content": (r['content'] if 'content' in r.keys() else '') or '',
            "created_at": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
        })

    # Built-in folders + any custom folders inferred from uploaded documents
    custom_folders = sorted({
        d.folder for d in documents if d.folder and d.folder not in ALLOWED_FOLDERS
    })
    all_folders = ALLOWED_FOLDERS + custom_folders

    hidden_folders = []
    try:
        if getattr(current_user, 'hidden_folders', None):
            parsed = json.loads(current_user.hidden_folders)
            if isinstance(parsed, list):
                hidden_folders = [str(x) for x in parsed if isinstance(x, str)]
    except (TypeError, ValueError):
        hidden_folders = []

    folders = [f for f in all_folders if f not in hidden_folders]

    folder_counts = {folder: 0 for folder in folders}
    for d in documents:
        if d.folder in folder_counts:
            folder_counts[d.folder] += 1

    return {
        "user": current_user,
        "documents": documents,
        "notes": notes,
        "folders": folders,
        "default_folders": [f for f in ALLOWED_FOLDERS if f not in hidden_folders],
        "hidden_folders": [f for f in ALLOWED_FOLDERS if f in hidden_folders],
        "folder_counts": folder_counts,
        "active_folder": active_folder,
        "folder_view": active_folder is not None,
        "trash": _trash_items_for_user(current_user.id),
    }


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', **_dashboard_context())


@app.route('/folders/<path:folder_name>')
@login_required
def folder_page(folder_name):
    """Dedicated page showing one folder and the files inside it."""
    context = _dashboard_context()
    wanted = (folder_name or "").strip()
    match = next(
        (name for name in context["folders"] if name.lower() == wanted.lower()),
        None,
    )
    if match is None:
        abort(404)
    context["active_folder"] = match
    context["folder_view"] = True
    return render_template('dashboard.html', **context)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        # Simple registration
        hashed = generate_password_hash(password)
        conn = get_db()
        try:
            conn.execute("INSERT INTO user (username, email, password_hash, name) VALUES (?, ?, ?, ?)",
                        (username, email.lower(), hashed, username))
            conn.commit()
            flash('Account created successfully! Please log in.')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Error: {str(e)}')
        finally:
            conn.close()
    return render_template('register.html')

# Add more routes as needed...
@app.route('/notes/add', methods=['POST'])
@login_required
def add_note():
    """Create a note from either a JSON fetch() call or a classic form post."""
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        title = (payload.get('title') or '').strip()
        content = payload.get('content') or ''
    else:
        title = (request.form.get('title') or '').strip()
        content = request.form.get('content') or ''

    title = title or 'Quick Note'

    if not str(content).strip():
        if request.is_json:
            return jsonify({"status": "error", "message": "Note content cannot be empty"}), 400
        flash('Note content cannot be empty')
        return redirect(url_for('dashboard'))

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO note (user_id, title, content) VALUES (?, ?, ?)",
        (current_user.id, title, content),
    )
    conn.commit()
    note_id = cur.lastrowid
    row = conn.execute(
        "SELECT id, title, content, created_at FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    conn.close()

    dt = _parse_dt(row['created_at']) if row else None
    created_at = dt.strftime("%Y-%m-%d %H:%M") if dt else ""

    if request.is_json:
        # dashboard.html expects {status, note:{id,title,content,created_at}}
        return jsonify({
            "status": "ok",
            "note": {
                "id": row['id'] if row else note_id,
                "title": (row['title'] if row else title) or 'Quick Note',
                "content": (row['content'] if row else content) or '',
                "created_at": created_at,
            },
        })

    flash('Note saved successfully!')
    return redirect(url_for('dashboard'))

@app.route('/documents/upload', methods=['POST'])
@login_required
def upload_document():
    folder = request.form.get('folder') or 'My Files'
    files = request.files.getlist('files') or request.files.getlist('file')
    if not files or all(f.filename == '' for f in files):
        flash('No file selected')
        return redirect(url_for('dashboard'))

    upload_folder = app.config['UPLOAD_FOLDER']
    os_mod.makedirs(upload_folder, exist_ok=True)

    conn = get_db()
    uploaded = 0
    for file in files:
        if file and file.filename:
            orig_name = secure_filename(file.filename) or 'document'
            unique_name = f"{uuid.uuid4().hex}_{orig_name}"
            save_path = os_mod.path.join(upload_folder, unique_name)
            file.save(save_path)
            file_size = os_mod.path.getsize(save_path)
            file_type = file.content_type or 'application/octet-stream'
            conn.execute(
                "INSERT INTO document (user_id, filename, original_filename, file_path, "
                "file_size, file_type, folder) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (current_user.id, unique_name, file.filename, save_path,
                 file_size, file_type, folder),
            )
            uploaded += 1
    conn.commit()
    conn.close()

    flash(f"Successfully uploaded {uploaded} file(s) to '{folder}'!")
    if (request.form.get('return_to') or '').strip():
        return redirect(url_for('folder_page', folder_name=folder))
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out')
    return redirect(url_for('index'))


@app.route('/documents/<int:doc_id>/download')
@login_required
def download_document(doc_id):
    conn = get_db()
    doc = conn.execute(
        "SELECT * FROM document WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (doc_id, current_user.id),
    ).fetchone()
    conn.close()
    if not doc:
        abort(404)

    resolved = _resolve_doc_file(doc)
    if not resolved:
        flash('That file is no longer available on disk.')
        return redirect(url_for('dashboard'))
    directory, filename = resolved

    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=doc['original_filename'] or filename,
    )


@app.route('/documents/<int:doc_id>/view')
@login_required
def preview_document(doc_id):
    """Owner-only in-browser preview for safe file types (no download)."""
    conn = get_db()
    doc = conn.execute(
        "SELECT * FROM document WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (doc_id, current_user.id),
    ).fetchone()
    conn.close()
    if not doc:
        abort(404)

    original = (doc['original_filename'] if 'original_filename' in doc.keys() else '') or \
               (doc['filename'] if 'filename' in doc.keys() else '')
    ext = os_mod.path.splitext(original)[1].lower()
    if ext not in PREVIEW_EXTENSIONS:
        abort(415)

    resolved = _resolve_doc_file(doc)
    if not resolved:
        abort(404)
    directory, filename = resolved
    full_path = os_mod.path.join(directory, filename)

    mimetype = PREVIEW_MIME_TYPES.get(ext)
    if not mimetype:
        abort(415)

    response = send_file(
        full_path,
        mimetype=mimetype,
        as_attachment=False,
        download_name=original or filename,
    )
    safe_name = secure_filename(original) or 'file'
    response.headers['Content-Disposition'] = f'inline; filename="{safe_name}"'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'private, no-store'
    if ext == '.pdf':
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
        )
    else:
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
        )
    return response


# --- Public share links ---
@app.route('/documents/<int:doc_id>/share', methods=['POST'])
@login_required
def create_share_link(doc_id):
    """Create a one-time or expiring public link for a live document."""
    conn = get_db()
    doc = conn.execute(
        "SELECT * FROM document WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (doc_id, current_user.id),
    ).fetchone()
    conn.close()
    if not doc:
        abort(404)

    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    expiry = (data.get('expiry') or '').strip().lower()
    usage = (data.get('usage') or '').strip().lower()

    if expiry not in SHARE_EXPIRY_MINUTES:
        return jsonify({"status": "error", "message": "Choose an expiry time."}), 400
    if usage not in SHARE_USAGE:
        return jsonify({"status": "error", "message": "Choose how often the link can be used."}), 400

    # The plaintext token only exists in this response; the DB stores its hash.
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=SHARE_EXPIRY_MINUTES[expiry])
    conn = get_db()
    conn.execute(
        "INSERT INTO share_link (user_id, document_id, token, expires_at, max_uses, use_count) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (current_user.id, doc['id'], _hash_share_token(token),
         expires_at.strftime("%Y-%m-%d %H:%M:%S.%f"), SHARE_USAGE[usage]),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "status": "ok",
        "url": url_for('shared_file', token=token, _external=True),
        "expires_in": _share_time_left(expires_at),
        "usage": usage,
    })


@app.route('/s/<token>')
def shared_file(token):
    """Public landing page for a share link (does not consume a download)."""
    link = _lookup_share_link(token)
    if link is None:
        abort(404)
    if _share_link_unavailable(link):
        return render_template('share_unavailable.html'), 410

    conn = get_db()
    doc = conn.execute(
        "SELECT * FROM document WHERE id = ?", (link['document_id'],)
    ).fetchone()
    conn.close()
    if doc is None:
        abort(404)
    if doc['deleted_at'] is not None:
        # Owner moved it to trash: sharing stops at once.
        return render_template('share_unavailable.html'), 410
    if not _resolve_doc_file(doc):
        abort(404)

    return render_template(
        'share.html',
        token=token,
        doc=_Doc(doc),
        size_label=_format_file_size(doc['file_size']),
        expires_in=_share_time_left(link['expires_at']),
        single_use=(link['max_uses'] == 1),
    )


@app.route('/s/<token>/download')
def shared_file_download(token):
    """Download a shared file once per allowed use, with an atomic counter."""
    link = _lookup_share_link(token)
    if link is None:
        abort(404)
    expires_at = _parse_dt(link['expires_at'])
    if expires_at and expires_at <= datetime.utcnow():
        return render_template('share_unavailable.html'), 410

    conn = get_db()
    doc = conn.execute(
        "SELECT * FROM document WHERE id = ?", (link['document_id'],)
    ).fetchone()
    if doc is None:
        conn.close()
        abort(404)
    if doc['deleted_at'] is not None:
        conn.close()
        return render_template('share_unavailable.html'), 410

    # Claim one use atomically so two simultaneous requests cannot both succeed.
    cur = conn.execute(
        "UPDATE share_link SET use_count = COALESCE(use_count, 0) + 1 "
        "WHERE id = ? AND expires_at > ? "
        "AND (max_uses IS NULL OR COALESCE(use_count, 0) < max_uses)",
        (link['id'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")),
    )
    conn.commit()
    claimed = cur.rowcount
    conn.close()
    if claimed != 1:
        return render_template('share_unavailable.html'), 410

    resolved = _resolve_doc_file(doc)
    if not resolved:
        abort(404)
    directory, filename = resolved

    # Always download as an opaque attachment: shared files are never rendered
    # inside the vault's origin, so an uploaded HTML/SVG file cannot run.
    response = send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=doc['original_filename'] or 'shared-file',
        mimetype='application/octet-stream',
    )
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'none'"
    return response


@app.route('/documents/delete', methods=['POST'])
@login_required
def delete_documents():
    if request.is_json:
        doc_ids = (request.get_json() or {}).get('ids', [])
    else:
        doc_ids = request.form.getlist('ids') or request.form.getlist('doc_ids')
        if not doc_ids and request.form.get('id'):
            doc_ids = [request.form.get('id')]

    if not doc_ids:
        if request.is_json:
            return jsonify({"status": "error", "message": "No document IDs provided"}), 400
        flash('No documents selected for deletion')
        return redirect(url_for('dashboard'))

    moved = 0
    conn = get_db()
    for doc_id in doc_ids:
        try:
            doc = conn.execute(
                "SELECT * FROM document WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                (int(doc_id), current_user.id),
            ).fetchone()
        except (ValueError, TypeError):
            continue
        if doc and _compress_into_trash(conn, doc):
            moved += 1
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({
            "status": "ok",
            "deleted_count": moved,
            "moved_to_trash": moved,
            "retention_days": TRASH_RETENTION_DAYS,
        })
    flash(
        f"Moved {moved} document(s) to Trash — permanently deleted after "
        f"{TRASH_RETENTION_DAYS} days",
        "success",
    )
    return redirect(url_for('dashboard'))


# --- Trash routes ---
@app.route('/trash/list')
@login_required
def list_trash():
    """Return the signed-in user's trash as JSON (used by the Trash modal)."""
    return jsonify({
        "status": "ok",
        "retention_days": TRASH_RETENTION_DAYS,
        "items": _trash_items_for_user(current_user.id),
    })


@app.route('/trash/restore', methods=['POST'])
@login_required
def restore_trash():
    """Restore trashed documents, decompressing them back byte for byte."""
    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    doc_ids = data.get('ids') or ([data.get('id')] if data.get('id') else [])
    if not doc_ids:
        return jsonify({"status": "error", "message": "No document IDs provided"}), 400

    restored = 0
    failed = []
    conn = get_db()
    for doc_id in doc_ids:
        try:
            doc = conn.execute(
                "SELECT * FROM document WHERE id = ? AND user_id = ? "
                "AND deleted_at IS NOT NULL",
                (int(doc_id), current_user.id),
            ).fetchone()
        except (ValueError, TypeError):
            continue
        if doc is None:
            continue
        if _restore_document_from_trash(conn, doc):
            restored += 1
        else:
            failed.append(doc['original_filename'])
    conn.commit()
    conn.close()

    message = f"Restored {restored} file(s)."
    if failed:
        message += " Could not restore: " + ", ".join(failed)
    return jsonify({
        "status": "ok" if restored else "error",
        "restored_count": restored,
        "failed": failed,
        "message": message,
    })


@app.route('/trash/purge', methods=['POST'])
@login_required
def purge_trash():
    """Permanently delete trashed documents now (cannot be undone)."""
    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    doc_ids = data.get('ids') or ([data.get('id')] if data.get('id') else [])
    if not doc_ids:
        return jsonify({"status": "error", "message": "No document IDs provided"}), 400

    purged = 0
    conn = get_db()
    for doc_id in doc_ids:
        try:
            doc = conn.execute(
                "SELECT * FROM document WHERE id = ? AND user_id = ? "
                "AND deleted_at IS NOT NULL",
                (int(doc_id), current_user.id),
            ).fetchone()
        except (ValueError, TypeError):
            continue
        if doc is not None:
            _purge_trashed_document(conn, doc)
            purged += 1
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "purged_count": purged})


@app.route('/trash/empty', methods=['POST'])
@login_required
def empty_trash():
    """Permanently delete every trashed document for this user."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM document WHERE user_id = ? AND deleted_at IS NOT NULL",
        (current_user.id,),
    ).fetchall()
    for doc in rows:
        _purge_trashed_document(conn, doc)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "purged_count": len(rows)})



@app.route('/notes/delete', methods=['POST'])
@login_required
def delete_notes():
    if request.is_json:
        note_ids = (request.get_json() or {}).get('ids', [])
    else:
        note_ids = request.form.getlist('ids') or request.form.getlist('note_ids')
        if not note_ids and request.form.get('id'):
            note_ids = [request.form.get('id')]

    if not note_ids:
        if request.is_json:
            return jsonify({"status": "error", "message": "No note IDs provided"}), 400
        flash('No notes selected for deletion')
        return redirect(url_for('dashboard'))

    deleted = 0
    conn = get_db()
    for note_id in note_ids:
        try:
            cur = conn.execute(
                "DELETE FROM note WHERE id = ? AND user_id = ?",
                (int(note_id), current_user.id),
            )
            deleted += cur.rowcount
        except (ValueError, TypeError):
            continue
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({"status": "ok", "deleted_count": deleted})
    flash(f'Deleted {deleted} note(s)')
    return redirect(url_for('dashboard'))


@app.route('/folders/remove', methods=['POST'])
@login_required
def remove_folder():
    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    folder = (data.get('name') or '').strip()
    if not folder:
        if request.is_json:
            return jsonify({"status": "error", "message": "Folder name required"}), 400
        flash('Folder name required')
        return redirect(url_for('dashboard'))

    conn = get_db()
    docs = conn.execute(
        "SELECT * FROM document WHERE user_id = ? AND deleted_at IS NULL "
        "AND lower(folder) = lower(?)",
        (current_user.id, folder),
    ).fetchall()
    deleted = 0
    for doc in docs:
        if _compress_into_trash(conn, doc):
            deleted += 1

    # Remember hidden built-in folders
    canonical = next((f for f in ALLOWED_FOLDERS if f.lower() == folder.lower()), None)
    if canonical:
        hidden = []
        try:
            if current_user.hidden_folders:
                parsed = json.loads(current_user.hidden_folders)
                if isinstance(parsed, list):
                    hidden = [str(x) for x in parsed if isinstance(x, str)]
        except (TypeError, ValueError):
            hidden = []
        if canonical not in hidden:
            hidden.append(canonical)
        conn.execute("UPDATE user SET hidden_folders = ? WHERE id = ?",
                     (json.dumps(hidden), current_user.id))
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({"status": "ok", "folder": canonical or folder,
                        "deleted_count": deleted, "moved_to_trash": deleted})
    flash(f"Removed folder '{canonical or folder}' — {deleted} file(s) moved to Trash")
    return redirect(url_for('dashboard'))


@app.route('/folders/restore', methods=['POST'])
@login_required
def restore_folder():
    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    folder = (data.get('name') or '').strip()
    canonical = next((f for f in ALLOWED_FOLDERS if f.lower() == folder.lower()), folder)

    hidden = []
    try:
        if current_user.hidden_folders:
            parsed = json.loads(current_user.hidden_folders)
            if isinstance(parsed, list):
                hidden = [str(x) for x in parsed if isinstance(x, str)]
    except (TypeError, ValueError):
        hidden = []
    hidden = [h for h in hidden if h.lower() != canonical.lower()]

    conn = get_db()
    conn.execute("UPDATE user SET hidden_folders = ? WHERE id = ?",
                 (json.dumps(hidden), current_user.id))
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({"status": "ok", "folder": canonical})
    flash(f"Restored folder '{canonical}'")
    return redirect(url_for('dashboard'))


@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    fields = {}
    for f in ("name", "phone", "date_of_birth", "gender", "location", "profile_photo"):
        if f in request.form:
            fields[f] = (request.form.get(f) or '').strip()

    # Avatar upload
    if "avatar_file" in request.files:
        avatar = request.files["avatar_file"]
        if avatar and avatar.filename:
            ext = secure_filename(avatar.filename).split('.')[-1]
            avatar_name = f"avatar_{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
            avatar.save(os_mod.path.join(app.config['UPLOAD_FOLDER'], avatar_name))
            fields['profile_photo'] = f"/uploads/{avatar_name}"

    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn = get_db()
        conn.execute(f"UPDATE user SET {sets} WHERE id = ?",
                     (*fields.values(), current_user.id))
        conn.commit()
        conn.close()

    if request.is_json:
        return jsonify({"status": "ok", "message": "Profile updated successfully"})
    flash('Profile updated successfully!')
    return redirect(url_for('dashboard'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    errors = []
    if request.method == 'POST':
        current_password = request.form.get('current_password') or ''
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        if not check_password_hash(current_user.password_hash, current_password):
            errors.append('Current password is incorrect')
        if len(new_password) < 6:
            errors.append('New password needs to be at least 6 characters')
        if new_password != confirm_password:
            errors.append('New passwords do not match')
        if not errors:
            conn = get_db()
            conn.execute("UPDATE user SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(new_password), current_user.id))
            conn.commit()
            conn.close()
            flash('Password updated successfully!')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html', errors=errors)


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(app.config['SECRET_KEY'])


def _get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'


def get_base_url():
    if os.environ.get('RENDER_EXTERNAL_URL'):
        return os.environ.get('RENDER_EXTERNAL_URL').strip().rstrip('/')

    configured_url = os.environ.get('APP_URL') or os.environ.get('BASE_URL')
    if configured_url and not (os.environ.get('RENDER') and ('192.168.' in configured_url or '10.' in configured_url or 'localhost' in configured_url or '127.0.0.1' in configured_url)):
        return configured_url.strip().rstrip('/')

    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host', request.host)
    # If request is from localhost / 127.0.0.1, resolve to local Wi-Fi IP so phones on same Wi-Fi can open it
    if ('127.0.0.1' in host or 'localhost' in host) and not os.environ.get('RENDER'):
        local_ip = _get_local_ip()
        port = host.split(':')[-1] if ':' in host else '5555'
        return f"{scheme}://{local_ip}:{port}"

    return f"{scheme}://{host}"


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    errors = []
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errors.append('Please enter a valid email address')
        else:
            conn = get_db()
            user = conn.execute("SELECT * FROM user WHERE lower(email) = ?", (email,)).fetchone()
            conn.close()
            if user:
                token = _serializer().dumps(user['email'], salt="password-reset-salt")
                base_url = get_base_url()
                reset_link = f"{base_url}/reset-password/{token}"
                print(f"\n[+] PASSWORD RESET LINK for {user['email']}: {reset_link}\n", flush=True)
                try:
                    from email_service import send_password_reset_email
                    result = send_password_reset_email(user['email'], reset_link)
                    if not result:
                        print(f"[!] Email dispatch notice: {result.message}", flush=True)
                        return render_template(
                            'forgot_password.html',
                            errors=errors,
                            reset_url=reset_link,
                            delivery_failed=True,
                            delivery_error=result.message,
                            submitted_email=user['email'],
                        )
                except Exception as mail_err:
                    print(f"[!] Email dispatch error: {mail_err}", flush=True)
                    return render_template(
                        'forgot_password.html',
                        errors=errors,
                        reset_url=reset_link,
                        delivery_failed=True,
                        delivery_error=str(mail_err),
                        submitted_email=user['email'],
                    )

            flash('If an account exists with that email address, a password reset link has been sent.')
            return redirect(url_for('login'))
    return render_template('forgot_password.html', errors=errors)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    from itsdangerous import SignatureExpired, BadTimeSignature
    try:
        email = _serializer().loads(token, salt="password-reset-salt", max_age=3600)
    except (SignatureExpired, BadTimeSignature):
        flash('The password reset link is invalid or has expired.')
        return redirect(url_for('forgot_password'))

    conn = get_db()
    user = conn.execute("SELECT * FROM user WHERE lower(email) = lower(?)", (email,)).fetchone()
    if not user:
        conn.close()
        flash('User account not found.')
        return redirect(url_for('forgot_password'))

    errors = []
    if request.method == 'POST':
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        if len(new_password) < 6:
            errors.append('Password needs to be at least 6 characters')
        if new_password != confirm_password:
            errors.append('Passwords do not match')
        if not errors:
            conn.execute("UPDATE user SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(new_password), user['id']))
            conn.commit()
            conn.close()
            flash('Password has been reset successfully! You can now log in.')
            return redirect(url_for('login'))
    conn.close()
    return render_template('reset_password.html', errors=errors)


@app.route('/account/delete', methods=['POST'])
@login_required
def delete_account():
    """Permanently erase the signed-in user's account and everything it owns."""
    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    password = data.get('password') or ''
    phrase = (data.get('phrase') or '').strip()

    def fail(message):
        if request.is_json:
            return jsonify({"status": "error", "message": message}), 400
        flash(message)
        return redirect(url_for('dashboard'))

    if not check_password_hash(current_user.password_hash, password):
        return fail("Current password is incorrect.")
    if phrase.upper() != DELETE_ACCOUNT_PHRASE:
        return fail(f'Type "{DELETE_ACCOUNT_PHRASE}" to confirm deletion.')

    user_id = current_user.id
    upload_folder = app.config['UPLOAD_FOLDER']
    photo = current_user.profile_photo or ''

    conn = get_db()
    # Fixed deletion order: share links -> documents/files -> notes -> avatar -> account.
    conn.execute("DELETE FROM share_link WHERE user_id = ?", (user_id,))
    docs = conn.execute(
        "SELECT * FROM document WHERE user_id = ?", (user_id,)
    ).fetchall()
    for doc in docs:
        for path in (doc['file_path'], doc['trash_path']):
            if path and os_mod.path.isfile(path):
                try:
                    os_mod.remove(path)
                except OSError:
                    pass
    conn.execute("DELETE FROM document WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM note WHERE user_id = ?", (user_id,))

    # Avatar
    if photo.startswith('/uploads/'):
        avatar_path = os_mod.path.join(upload_folder, os_mod.path.basename(photo))
        if os_mod.path.isfile(avatar_path):
            try:
                os_mod.remove(avatar_path)
            except OSError:
                pass
    conn.execute("UPDATE user SET profile_photo = NULL WHERE id = ?", (user_id,))

    # Security logs are kept for auditing, but the identity is masked.
    try:
        conn.execute(
            "UPDATE security_log SET user_id = NULL, login_id = 'deleted-account' "
            "WHERE user_id = ?",
            (user_id,),
        )
    except sqlite3.OperationalError:
        pass

    conn.execute("DELETE FROM user WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    logout_user()
    if request.is_json:
        return jsonify({"status": "ok"})
    flash("Your account has been permanently deleted.")
    return redirect(url_for('login'))


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    if not filename.startswith(f"avatar_{current_user.id}_"):
        abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/health/db')
def health_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        conn.close()
        return {'db': 'ok', 'build': BUILD_TAG}, 200
    except Exception as e:
        return {'db': 'error', 'detail': str(e), 'build': BUILD_TAG}, 500


def _port_in_use(port):
    """True when something is already accepting connections on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port(port):
    """
    Terminates any stale process occupying the target port.
    Returns True if port is now free, False otherwise.
    """
    if not _port_in_use(port):
        return True

    current_pid = os.getpid()
    pids_to_kill = set()

    # Strategy 1: netstat -ano -p tcp
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

    # Strategy 2: PowerShell Get-NetTCPConnection (if netstat missed it)
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

    # Wait briefly for OS to release the socket
    for _ in range(6):
        time.sleep(0.3)
        if not _port_in_use(port):
            return True

    return not _port_in_use(port)


def _ensure_available_port(desired_port):
    """
    Ensures an available port by first attempting to kill any stale process on desired_port,
    and if that fails, automatically finding the next available port.
    """
    if not _port_in_use(desired_port):
        return desired_port

    print(f"  [!] Port {desired_port} is already in use. Attempting to free it...", flush=True)
    if _free_port(desired_port):
        print(f"  [+] Successfully freed port {desired_port}.", flush=True)
        return desired_port

    # If still in use, fallback to next available port
    curr_port = desired_port + 1
    while _port_in_use(curr_port):
        curr_port += 1

    print(f"  [i] Port {desired_port} could not be freed. Switched to next available port: {curr_port}", flush=True)
    return curr_port


# Run the app
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5555))

    # Windows lets several processes bind the same port, so a forgotten old
    # server silently keeps serving stale code. Automatically free the port
    # or select the next free port instead of failing.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        port = _ensure_available_port(port)
        os.environ['PORT'] = str(port)

    print("\n" + "=" * 70, flush=True)
    print("  [+] SECURE VAULT SYSTEM IS RUNNING (Minimal mode)", flush=True)
    print("=" * 70, flush=True)
    print(f"  * Build:              {BUILD_TAG}", flush=True)
    print(f"  * Local PC Access:    http://127.0.0.1:{port}", flush=True)
    print("=" * 70 + "\n", flush=True)
    app.run(host="0.0.0.0", port=port, debug=True)
