import re
from datetime import timedelta

from flask import Flask, render_template, url_for, request, redirect, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()
login_manager = LoginManager()

RESET_SALT = "password-reset-salt"
RESET_MAX_AGE = 3600  # seconds (1 hour)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f"<User {self.username}>"


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = 'dev-secret-key'
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///app.db"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=15)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

    @app.route("/health/db")
    def health_db():
        try:
            db.session.execute(text("SELECT 1"))
            return {"db": "ok"}, 200
        except Exception as e:
            return {"db": "error", "detail": str(e)}, 500

    with app.app_context():
        db.create_all()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/test")
    @login_required
    def test():
        return "TEST ROUTE"

    @app.route("/register", methods=["GET", "POST"])
    def register():
        errors = []

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if not (3 <= len(username) <= 80):
                errors.append("Username must be between 3 and 80 characters")

            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                errors.append("Please enter a valid email address")

            if len(password) < 6:
                errors.append("Password needs to be at least 6 characters")

            if password != confirm_password:
                errors.append("Passwords don't match")

            if not errors:
                try:
                    pw_hash = generate_password_hash(password)
                    user = User(username=username, email=email, password_hash=pw_hash)
                    db.session.add(user)
                    db.session.commit()

                    flash("Account created successfully!", "success")
                    return redirect(url_for('login'))

                except IntegrityError:
                    db.session.rollback()
                    errors.append("That username or email is already registered")

        return render_template("register.html", errors=errors)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        errors = []

        if request.method == "POST":
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""

            if not email:
                errors.append("Email is required")

            if not password:
                errors.append("Password is required")

            if not errors:
                user = User.query.filter_by(email=email).first()

                if not user or not check_password_hash(user.password_hash, password):
                    errors.append("Invalid email or password")
                else:
                    remember_flag = request.form.get("remember") == "1"
                    login_user(user, remember=remember_flag)

                    next_page = request.form.get("next")
                    return redirect(next_page or url_for("dashboard"))

        return render_template("login.html", errors=errors)

    @app.route("/logout")
    def logout():
        logout_user()
        flash("You have been logged out", "success")
        return redirect(url_for("index"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip()
            user = User.query.filter_by(email=email).first()

            if user:
                token = serializer.dumps(user.email, salt=RESET_SALT)
                reset_url = url_for("reset_password", token=token, _external=True)
                # No email provider is wired up yet, so the link is printed
                # to the console for testing. Swap this for a real send
                # (Flask-Mail, SendGrid, SES, etc.) before going to production.
                print(f"[DEV] Password reset link for {user.email}: {reset_url}")

            # Same message whether or not the email exists, so this form
            # can't be used to check which emails are registered.
            flash("If that email is registered, a reset link has been sent.", "success")
            return redirect(url_for("login"))

        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        try:
            email = serializer.loads(token, salt=RESET_SALT, max_age=RESET_MAX_AGE)
        except SignatureExpired:
            flash("That reset link has expired. Please request a new one.", "error")
            return redirect(url_for("forgot_password"))
        except BadSignature:
            flash("That reset link is invalid.", "error")
            return redirect(url_for("forgot_password"))

        errors = []

        if request.method == "POST":
            password = request.form.get("password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if len(password) < 6:
                errors.append("Password needs to be at least 6 characters")

            if password != confirm_password:
                errors.append("Passwords don't match")

            if not errors:
                user = User.query.filter_by(email=email).first()
                if not user:
                    flash("That account no longer exists.", "error")
                    return redirect(url_for("register"))

                user.password_hash = generate_password_hash(password)
                db.session.commit()

                flash("Your password has been reset. Please log in.", "success")
                return redirect(url_for("login"))

        return render_template("reset_password.html", errors=errors, token=token)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5555)
