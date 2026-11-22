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
import sqlite3
import json
import uuid
import re
import socket
from datetime import datetime

# Bump this whenever the runner changes so you can confirm which code is live
# by opening http://127.0.0.1:5555/health/db
BUILD_TAG = "minimal-2 (preview + robust download)"

# Create Flask app (no SQLAlchemy import needed for basic operation)
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# Use a simple SECRET_KEY - the app will work with limited functionality
app.config['SECRET_KEY'] = 'dev-secret-key-vault-123'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Uploads directory (matches original app.py behaviour)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_UPLOAD_DIR = os.path.join(_BASE_DIR, 'uploads')
os.makedirs(_UPLOAD_DIR, exist_ok=True)
app.config['UPLOAD_FOLDER'] = _UPLOAD_DIR
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
    ):
        if col not in existing_cols:
            try:
                cur.execute(ddl)
            except Exception:
                pass
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
        email = request.form.get('email')
        password = request.form.get('password')
        # Simple check
        conn = get_db()
        row = conn.execute("SELECT * FROM user WHERE email = ?", (email.lower(),)).fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            login_user(_MinimalUser(row))
            return redirect(url_for('dashboard'))
        else:
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
        "SELECT * FROM document WHERE user_id = ? ORDER BY id DESC", (current_user.id,)
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
        "SELECT * FROM document WHERE id = ? AND user_id = ?",
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
        "SELECT * FROM document WHERE id = ? AND user_id = ?",
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

    deleted = 0
    conn = get_db()
    for doc_id in doc_ids:
        try:
            doc = conn.execute(
                "SELECT * FROM document WHERE id = ? AND user_id = ?",
                (int(doc_id), current_user.id),
            ).fetchone()
        except (ValueError, TypeError):
            continue
        if doc:
            if doc['file_path'] and os_mod.path.exists(doc['file_path']):
                try:
                    os_mod.remove(doc['file_path'])
                except OSError:
                    pass
            conn.execute("DELETE FROM document WHERE id = ?", (doc['id'],))
            deleted += 1
    conn.commit()
    conn.close()

    if request.is_json:
        return jsonify({"status": "ok", "deleted_count": deleted})
    flash(f'Deleted {deleted} document(s)')
    return redirect(url_for('dashboard'))


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
        "SELECT * FROM document WHERE user_id = ? AND lower(folder) = lower(?)",
        (current_user.id, folder),
    ).fetchall()
    deleted = 0
    for doc in docs:
        if doc['file_path'] and os_mod.path.exists(doc['file_path']):
            try:
                os_mod.remove(doc['file_path'])
            except OSError:
                pass
        conn.execute("DELETE FROM document WHERE id = ?", (doc['id'],))
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
                        "deleted_count": deleted})
    flash(f"Removed folder '{canonical or folder}' ({deleted} file(s) deleted)")
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
                reset_link = url_for('reset_password', token=token, _external=True)
                print(f"\n[+] PASSWORD RESET LINK for {user['email']}: {reset_link}\n", flush=True)
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


# Run the app
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5555))

    # Windows lets several processes bind the same port, so a forgotten old
    # server silently keeps serving stale code. Refuse to start a duplicate.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and _port_in_use(port):
        print("\n" + "=" * 70, flush=True)
        print(f"  [!] PORT {port} IS ALREADY IN USE", flush=True)
        print("=" * 70, flush=True)
        print("  Another Secure Vault server is already running on this port,", flush=True)
        print("  so this new one would serve stale code. Do one of these:", flush=True)
        print("", flush=True)
        print("    1. Stop the other server (Ctrl+C in its terminal), then run again.", flush=True)
        print("    2. Or use a different port:", flush=True)
        print(f"         $env:PORT=5556; python run_minimal.py", flush=True)
        print("=" * 70 + "\n", flush=True)
        sys.exit(1)

    print("\n" + "=" * 70, flush=True)
    print("  [+] SECURE VAULT SYSTEM IS RUNNING (Minimal mode)", flush=True)
    print("=" * 70, flush=True)
    print(f"  * Build:              {BUILD_TAG}", flush=True)
    print(f"  * Local PC Access:    http://127.0.0.1:{port}", flush=True)
    print("=" * 70 + "\n", flush=True)
    app.run(host="0.0.0.0", port=port, debug=True)