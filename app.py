import os
import re
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
    send_from_directory, jsonify, abort
)
from flask_login import (
    LoginManager, login_user, login_required, current_user, logout_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

from models import db, User, Document, Note, SecurityLog
from encryption import encrypt_note, decrypt_note
from email_service import send_password_reset_email

login_manager = LoginManager()

ALLOWED_FOLDERS = ['Identity', 'Finance', 'Health', 'Legal', 'Property', 'Misc']


def create_app():
    # Automatically locate templates whether uploaded inside templates/ or directly in root
    root_dir = os.path.dirname(os.path.abspath(__file__))
    tpl_dir = 'templates' if os.path.isdir(os.path.join(root_dir, 'templates')) else '.'
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
        if request.method == "POST":
            login_id = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""

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
                    errors.append("Invalid email or password")
                    try:
                        log = SecurityLog(
                            user_id=user.id if user else None,
                            login_id=email,
                            ip_address=request.remote_addr,
                            status="FAILED",
                            user_agent=request.user_agent.string
                        )
                        db.session.add(log)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

        return render_template("login.html", errors=errors)

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
                    sent = send_password_reset_email(user.email, reset_link)
                    if not sent:
                        print(f"[!] Email dispatch failed for: {user.email}")
                else:
                    print(f"[!] Password reset requested but email NOT found in database: {email}. Make sure the user is registered on this instance.")

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
    @app.route("/dashboard")
    @login_required
    def dashboard():
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

        # Calculate counts per folder
        folder_counts = {folder: 0 for folder in ALLOWED_FOLDERS}
        for doc in documents:
            if doc.folder in folder_counts:
                folder_counts[doc.folder] += 1

        return render_template(
            "dashboard.html",
            user=current_user,
            documents=documents,
            notes=notes,
            folders=ALLOWED_FOLDERS,
            folder_counts=folder_counts
        )

    # --- Document Routes ---
    @app.route("/documents/upload", methods=["POST"])
    @login_required
    def upload_document():
        folder = request.form.get("folder", "Misc")
        if folder not in ALLOWED_FOLDERS:
            folder = "Misc"

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

    @app.route('/uploads/<filename>')
    @login_required
    def uploaded_file(filename):
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
