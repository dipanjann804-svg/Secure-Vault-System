import os
import re
import json
import uuid
import socket
import time
import subprocess
import threading
import atexit
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
from flask import (
    Flask, render_template, url_for, request, redirect, flash, 
    send_from_directory, send_file, jsonify, abort
)
from flask_login import (
    LoginManager, login_user, login_required, current_user, logout_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text, func, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

from models import db, User, Document, Note, ShareLink, SecurityLog
from encryption import encrypt_note, decrypt_note
from email_service import send_password_reset_email

login_manager = LoginManager()

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
    db_uri = os.environ.get('DATABASE_URL')
    if db_uri and db_uri.startswith("postgres://"):
        db_uri = db_uri.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri or "sqlite:///app.db"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    if os.environ.get("RENDER") and not db_uri:
        print("\n" + "=" * 70, flush=True)
        print("  [!] WARNING: RUNNING WITH SQLITE ON RENDER", flush=True)
        print("=" * 70, flush=True)
        print("  Render free instances use ephemeral disks.", flush=True)
        print("  User accounts will be erased whenever the instance spins down or redeploys!", flush=True)
        print("  To persist data permanently, attach a PostgreSQL database and set DATABASE_URL.", flush=True)
        print("=" * 70 + "\n", flush=True)
    
    # Uploads configuration
    upload_folder = os.path.join(app.root_path, 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = upload_folder
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    with app.app_context():
        try:
            db.create_all()
            _sync_schema()
        except Exception as e:
            print(f"[!] Warning during db.create_all: {e}")

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

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
            login_id = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            key = _lockout_key(login_id, request.remote_addr)

            # 1) Check active lockout first (time.py: "Too many failed attempts...")
            disp = _lockout_display(key)
            lockout_remaining = disp["remaining"]
            if lockout_remaining > 0:
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
        configured_url = os.environ.get('APP_URL') or os.environ.get('BASE_URL')
        if configured_url:
            return configured_url.strip().rstrip('/')

        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        host = request.headers.get('X-Forwarded-Host', request.host)
        if '127.0.0.1' in host or 'localhost' in host:
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
                        flash(
                            f"Email delivery error: {result.message}",
                            "error"
                        )
                        return redirect(url_for("forgot_password"))
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
        # Fetch user's documents
        documents = Document.query.filter_by(user_id=current_user.id).order_by(Document.created_at.desc()).all()

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
        doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()
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
        doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()

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

        deleted_count = 0
        for doc_id in doc_ids:
            try:
                doc = Document.query.filter_by(id=int(doc_id), user_id=current_user.id).first()
                if doc:
                    if doc.file_path and os.path.exists(doc.file_path):
                        try:
                            os.remove(doc.file_path)
                        except OSError:
                            pass
                    db.session.delete(doc)
                    deleted_count += 1
            except (ValueError, TypeError):
                continue

        db.session.commit()

        if request.is_json:
            return jsonify({"status": "ok", "deleted_count": deleted_count})

        flash(f"Deleted {deleted_count} document(s)", "success")
        return redirect(url_for("dashboard"))

    # --- Folder Routes ---
    @app.route("/folders/remove", methods=["POST"])
    @login_required
    def remove_folder():
        data = (request.get_json(silent=True) or {}) if request.is_json else request.form
        folder = _clean_folder_name(data.get("name"))

        # Delete every file the user has in this folder
        docs = Document.query.filter_by(user_id=current_user.id).filter(
            func.lower(Document.folder) == folder.lower()
        ).all()
        deleted_count = 0
        for doc in docs:
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except OSError:
                    pass
            db.session.delete(doc)
            deleted_count += 1

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
            })

        flash(f"Removed folder '{canonical or folder}' ({deleted_count} file(s) deleted)", "success")
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

        encrypted_content = encrypt_note(str(content).strip())
        note = Note(
            user_id=current_user.id,
            title=str(title).strip() or "Quick Note",
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
        # 1) Share links
        ShareLink.query.filter_by(user_id=user_id).delete(synchronize_session=False)

        # 2) Documents and the files they point at
        for doc in Document.query.filter_by(user_id=user_id).all():
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
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
