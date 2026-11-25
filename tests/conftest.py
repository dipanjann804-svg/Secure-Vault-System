"""Shared fixtures for the Secure Vault test suite.

Everything here is isolated from the real application:

* ``DATABASE_URL`` points at a throwaway SQLite file in a temp directory.
* ``UPLOAD_FOLDER`` and ``TRASH_FOLDER`` point at temp directories, so the real
  ``uploads/`` and ``app.db`` are never touched.
* ``ENCRYPTION_KEY`` is a test-only Fernet key.

``app.py`` builds the Flask app at import time, so the environment has to be set
*before* ``import app`` happens. ``conftest.py`` is imported by pytest first.
"""
import base64
import hashlib
import os
import pathlib
import re
import sys
import tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="vault-tests-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_TMP / "test.db").as_posix()
os.environ["UPLOAD_FOLDER"] = str(_TMP / "uploads")
os.environ["TRASH_FOLDER"] = str(_TMP / "trash")
os.environ["SECRET_KEY"] = "test-secret-key-for-secure-vault-tests"
os.environ["ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    hashlib.sha256(b"secure-vault-test-key").digest()
).decode()
# Keep the CSRF window wide enough for a slow test run.
os.environ["WTF_CSRF_TIME_LIMIT"] = "3600"
os.environ["VOICE_RATE_LIMIT"] = "20"
os.environ["VOICE_RATE_WINDOW_SECONDS"] = "60"

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import app as flask_app  # noqa: E402
from models import (  # noqa: E402
    Contact,
    Document,
    Note,
    ShareAccess,
    ShareLink,
    ShareLinkItem,
    User,
    VoiceContext,
    db,
)

CSRF_META_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
CSRF_INPUT_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

TEST_PASSWORD = "Passw0rd!23"


@pytest.fixture(scope="session")
def app():
    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


class VaultClient:
    """Small helper that mirrors what the browser does, CSRF included."""

    def __init__(self, app, client=None):
        self.app = app
        self.client = client or app.test_client()
        self.csrf = None

    # --- CSRF -------------------------------------------------------------
    def refresh_csrf(self, path="/dashboard"):
        html = self.client.get(path).get_data(as_text=True)
        match = CSRF_META_RE.search(html) or CSRF_INPUT_RE.search(html)
        self.csrf = match.group(1) if match else None
        return self.csrf

    def headers(self, csrf=True):
        return {"X-CSRFToken": self.csrf} if csrf else {}

    # --- auth -------------------------------------------------------------
    def login(self, user, password=TEST_PASSWORD):
        page = self.client.get("/login").get_data(as_text=True)
        match = CSRF_META_RE.search(page)
        token = match.group(1) if match else None
        response = self.client.post(
            "/login",
            data={"email": user.email, "password": password, "csrf_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.get_data(as_text=True)[:400]
        self.refresh_csrf()
        return response

    # --- requests ---------------------------------------------------------
    def get(self, path, **kwargs):
        return self.client.get(path, **kwargs)

    def post_json(self, path, payload=None, csrf=True):
        return self.client.post(
            path, json=payload or {}, headers=self.headers(csrf)
        )

    def post_form(self, path, payload=None, csrf=True):
        return self.client.post(path, data=payload or {}, headers=self.headers(csrf))

    def voice(self, text, nonce=None, csrf=True):
        return self.post_json(
            "/voice/command", {"text": text, "nonce": nonce}, csrf=csrf
        )


@pytest.fixture()
def vault(app):
    return VaultClient(app)


@pytest.fixture()
def make_user(app):
    created = []

    def _make(name=None, email=None, password=TEST_PASSWORD):
        suffix = os.urandom(4).hex()
        username = name or f"user_{suffix}"
        address = email or f"{username}@example.test"
        with app.app_context():
            user = User(
                username=username,
                email=address,
                password_hash=generate_password_hash(password),
                name=username,
            )
            db.session.add(user)
            db.session.commit()
            created.append(user.id)
            return user.id

    return _make


@pytest.fixture()
def login_as(app, make_user):
    """Return (VaultClient, SimpleNamespace(id, username, email)) signed in."""

    def _login(name=None, email=None, password=TEST_PASSWORD):
        user_id = make_user(name=name, email=email, password=password)
        with app.app_context():
            user = db.session.get(User, user_id)
            identity = SimpleNamespace(
                id=user.id, username=user.username, email=user.email
            )
        client = VaultClient(app)
        client.login(identity, password=password)
        return client, identity

    return _login


@pytest.fixture()
def make_document(app):
    def _make(user_id, name="passport.pdf", folder="Identity", payload=b"%PDF-1.4 test bytes"):
        with app.app_context():
            stored = f"{os.urandom(8).hex()}_{name}"
            path = os.path.join(app.config["UPLOAD_FOLDER"], stored)
            with open(path, "wb") as handle:
                handle.write(payload)
            doc = Document(
                user_id=user_id,
                filename=stored,
                original_filename=name,
                file_path=path,
                file_size=len(payload),
                file_type="application/pdf",
                folder=folder,
            )
            db.session.add(doc)
            db.session.commit()
            return doc.id

    return _make


@pytest.fixture()
def make_contact(app):
    def _make(user_id, name="Rahul", email=None):
        with app.app_context():
            contact = Contact(
                user_id=user_id,
                name=name,
                email=email or f"{name.lower()}@example.test",
            )
            db.session.add(contact)
            db.session.commit()
            return contact.id

    return _make


@pytest.fixture(autouse=True)
def _clear_rate_limiters(app):
    """The in-memory share limiters must not leak between tests."""
    import app as app_module

    app_module._share_create_attempts.clear()
    app_module._share_password_attempts.clear()
    yield


def pending_nonce(app, user_id):
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user_id).first()
        return ctx.pending_nonce if ctx else None
