"""Regression tests for the features the voice assistant builds on.

Covers sharing, contacts, folders, trash, notes, account deletion and dashboard
rendering, so the voice work cannot silently break them.
"""
import io
import os
import re
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse

from models import (
    Contact,
    Document,
    Note,
    SecurityLog,
    ShareAccess,
    ShareLink,
    ShareLinkItem,
    User,
    VoiceContext,
    db,
)
from encryption import decrypt_note

CSRF_INPUT_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _upload(vault, name="passport.pdf", folder="Identity", payload=b"%PDF-1.4 test bytes"):
    return vault.client.post(
        "/documents/upload",
        data={"folder": folder, "files": (io.BytesIO(payload), name)},
        content_type="multipart/form-data",
        headers=vault.headers(),
    )


def _doc_id(app, user_id, name="passport.pdf"):
    with app.app_context():
        doc = Document.query.filter_by(user_id=user_id, original_filename=name).first()
        return doc.id if doc else None


def _page_token(html):
    match = CSRF_INPUT_RE.search(html)
    return match.group(1) if match else None


# --- Dashboard / auth -----------------------------------------------------
def test_dashboard_renders_voice_panel(login_as):
    vault, _user = login_as()
    response = vault.get("/dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="voicePanel"' in html
    assert 'id="voiceFab"' in html
    assert "js/voice.js" in html
    assert "js/csrf.js" in html
    assert 'name="csrf-token"' in html


def test_dashboard_requires_login(app):
    client = app.test_client()
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_health_endpoint(app):
    response = app.test_client().get("/health/db")
    assert response.status_code == 200
    assert response.get_json()["db"] == "ok"


# --- Uploads / preview ----------------------------------------------------
def test_upload_download_and_preview_guard(login_as, app):
    vault, user = login_as()
    assert _upload(vault).status_code == 302
    doc_id = _doc_id(app, user.id)
    assert doc_id is not None

    download = vault.get(f"/documents/{doc_id}/download")
    assert download.status_code == 200
    assert download.data == b"%PDF-1.4 test bytes"

    preview = vault.get(f"/documents/{doc_id}/view")
    assert preview.status_code == 200
    assert preview.headers["Cache-Control"] == "private, no-store"

    _upload(vault, name="notes.txt", payload=b"plain text")
    txt_id = _doc_id(app, user.id, "notes.txt")
    assert vault.get(f"/documents/{txt_id}/view").status_code == 415


def test_document_download_is_owner_scoped(login_as, make_document):
    owner_a, user_a = login_as()
    doc_id = make_document(user_a.id, name="passport.pdf")
    owner_b, _user_b = login_as()
    assert owner_b.get(f"/documents/{doc_id}/download").status_code == 404


# --- Contacts -------------------------------------------------------------
def test_contacts_add_list_and_duplicate(login_as):
    vault, _user = login_as()
    created = vault.post_json("/contacts", {"name": "Rahul", "email": "rahul@example.test"})
    assert created.status_code == 200
    assert created.get_json()["contact"]["name"] == "Rahul"

    duplicate = vault.post_json("/contacts", {"name": "Rahul", "email": "rahul@example.test"})
    assert duplicate.status_code == 409

    invalid = vault.post_json("/contacts", {"name": "Bad", "email": "not-an-email"})
    assert invalid.status_code == 400

    listed = vault.get("/contacts").get_json()
    assert [row["name"] for row in listed["contacts"]] == ["Rahul"]


def test_contacts_require_csrf(login_as):
    vault, _user = login_as()
    response = vault.post_json("/contacts", {"name": "X", "email": "x@example.test"}, csrf=False)
    assert response.status_code == 400


# --- Sharing --------------------------------------------------------------
def test_document_share_with_password_single_use(login_as, app, make_document, make_contact):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf", payload=b"secret payload")
    contact_id = make_contact(user.id, name="Rahul")

    response = vault.post_json(f"/documents/{doc_id}/share", {
        "expiry": "1h",
        "usage": "once",
        "password": "hunter2pass",
        "contact_id": str(contact_id),
    })
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["has_password"] is True
    assert body["recipient"] == "Rahul"

    path = urlparse(body["url"]).path
    public = app.test_client()
    page = public.get(path)
    assert page.status_code == 200
    assert "password" in page.get_data(as_text=True).lower()

    wrong = public.post(path, data={"password": "wrong", "csrf_token": _page_token(page.get_data(as_text=True))})
    assert wrong.status_code == 401

    page = public.get(path)
    granted = public.post(path, data={
        "password": "hunter2pass",
        "csrf_token": _page_token(page.get_data(as_text=True)),
    })
    assert granted.status_code == 302

    page = public.get(path)
    assert page.status_code == 200
    token = _page_token(page.get_data(as_text=True))
    download = public.post(path + "/download", data={"csrf_token": token})
    assert download.status_code == 200
    assert download.data == b"secret payload"
    assert download.headers["Cache-Control"] == "no-store"

    second = public.post(path + "/download", data={"csrf_token": token})
    assert second.status_code == 410

    with app.app_context():
        link = ShareLink.query.filter_by(user_id=user.id).one()
        assert link.use_count == 1
        assert ShareAccess.query.filter_by(share_id=link.id).count() >= 3


def test_share_requires_valid_options(login_as, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id)
    bad_expiry = vault.post_json(f"/documents/{doc_id}/share", {"expiry": "5y", "usage": "once"})
    assert bad_expiry.status_code == 400
    bad_usage = vault.post_json(f"/documents/{doc_id}/share", {"expiry": "1h", "usage": "always"})
    assert bad_usage.status_code == 400


def test_folder_share_builds_a_frozen_bundle(login_as, app, make_document):
    vault, user = login_as()
    make_document(user.id, name="a.pdf", payload=b"AAA")
    make_document(user.id, name="b.pdf", payload=b"BBB")

    response = vault.post_json("/folders/share", {"name": "Identity", "expiry": "24h", "usage": "once"})
    body = response.get_json()
    assert response.status_code == 200
    assert body["kind"] == "folder"
    assert body["file_count"] == 2

    with app.app_context():
        link = ShareLink.query.filter_by(user_id=user.id).one()
        assert link.share_kind == "folder"
        assert len(link.items) == 2
        assert link.folder == "Identity"

    path = urlparse(body["url"]).path
    public = app.test_client()
    page = public.get(path)
    token = _page_token(page.get_data(as_text=True))
    bundle = public.post(path + "/download", data={"csrf_token": token})
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.data)) as archive:
        assert sorted(archive.namelist()) == ["a.pdf", "b.pdf"]


def test_revoked_share_stops_working(login_as, app, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id)
    body = vault.post_json(f"/documents/{doc_id}/share", {"expiry": "1h", "usage": "multi"}).get_json()
    path = urlparse(body["url"]).path

    with app.app_context():
        share_id = ShareLink.query.filter_by(user_id=user.id).one().id

    revoke = vault.post_form(f"/shares/{share_id}/revoke")
    assert revoke.status_code == 302
    assert app.test_client().get(path).status_code == 410


def test_share_page_is_owner_scoped_and_not_cached(login_as, app, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id)
    body = vault.post_json(f"/documents/{doc_id}/share", {"expiry": "1h", "usage": "multi"}).get_json()
    path = urlparse(body["url"]).path
    response = app.test_client().get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_shares_page_lists_links(login_as, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf")
    vault.post_json(f"/documents/{doc_id}/share", {"expiry": "1h", "usage": "multi"})
    page = vault.get("/shares")
    assert page.status_code == 200
    assert "passport.pdf" in page.get_data(as_text=True)


# --- Trash ----------------------------------------------------------------
def test_trash_move_restore_and_purge(login_as, app, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf", payload=b"trash me")

    deleted = vault.post_json("/documents/delete", {"ids": [doc_id]}).get_json()
    assert deleted["moved_to_trash"] == 1

    listing = vault.get("/trash/list").get_json()
    assert [item["name"] for item in listing["items"]] == ["passport.pdf"]

    restored = vault.post_json("/trash/restore", {"ids": [doc_id]}).get_json()
    assert restored["restored_count"] == 1
    assert vault.get(f"/documents/{doc_id}/download").data == b"trash me"

    vault.post_json("/documents/delete", {"ids": [doc_id]})
    purged = vault.post_json("/trash/purge", {"ids": [doc_id]}).get_json()
    assert purged["purged_count"] == 1
    with app.app_context():
        assert db.session.get(Document, doc_id) is None


def test_trashing_a_document_revokes_its_share(login_as, app, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id)
    body = vault.post_json(f"/documents/{doc_id}/share", {"expiry": "1h", "usage": "multi"}).get_json()
    path = urlparse(body["url"]).path
    assert app.test_client().get(path).status_code == 200

    vault.post_json("/documents/delete", {"ids": [doc_id]})
    # A single-document link is deleted with its access rows, so it is gone.
    assert app.test_client().get(path).status_code in (404, 410)
    with app.app_context():
        assert ShareLink.query.filter_by(user_id=user.id).count() == 0


def test_trash_is_owner_scoped(login_as, make_document):
    owner_a, user_a = login_as()
    doc_id = make_document(user_a.id)
    owner_a.post_json("/documents/delete", {"ids": [doc_id]})

    owner_b, _user_b = login_as()
    assert owner_b.get("/trash/list").get_json()["items"] == []
    assert owner_b.post_json("/trash/restore", {"ids": [doc_id]}).get_json()["restored_count"] == 0


# --- Folders --------------------------------------------------------------
def test_folder_page_and_remove(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf", folder="Finance")
    assert vault.get("/folders/Finance").status_code == 200
    assert vault.get("/folders/Nope").status_code == 404

    removed = vault.post_json("/folders/remove", {"name": "Finance"}).get_json()
    assert removed["moved_to_trash"] == 1


# --- Notes ----------------------------------------------------------------
def test_notes_are_encrypted_at_rest(login_as, app):
    vault, user = login_as()
    created = vault.post_json("/notes/add", {"title": "Renewals", "content": "expires in June"})
    assert created.status_code == 200
    with app.app_context():
        note = Note.query.filter_by(user_id=user.id).one()
        assert "expires in June" not in note.content
        assert decrypt_note(note.content) == "expires in June"
    note_id = created.get_json()["note"]["id"]
    deleted = vault.post_json("/notes/delete", {"ids": [note_id]})
    assert deleted.get_json()["deleted_count"] == 1


# --- Account deletion -----------------------------------------------------
def test_account_deletion_clears_voice_context_and_data(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    vault.post_json("/notes/add", {"title": "Temp", "content": "temporary"})
    vault.get("/voice/state")
    with app.app_context():
        assert VoiceContext.query.filter_by(user_id=user.id).count() == 1

    response = vault.post_json("/account/delete", {
        "password": "Passw0rd!23",
        "phrase": "DELETE MY ACCOUNT",
    })
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

    with app.app_context():
        assert db.session.get(User, user.id) is None
        assert VoiceContext.query.filter_by(user_id=user.id).count() == 0
        assert Document.query.filter_by(user_id=user.id).count() == 0
        assert Note.query.filter_by(user_id=user.id).count() == 0
        assert Contact.query.filter_by(user_id=user.id).count() == 0
        assert SecurityLog.query.filter_by(user_id=user.id).count() == 0


def test_account_deletion_requires_password_and_phrase(login_as, app):
    vault, user = login_as()
    wrong = vault.post_json("/account/delete", {"password": "nope", "phrase": "DELETE MY ACCOUNT"})
    assert wrong.status_code == 400
    missing_phrase = vault.post_json("/account/delete", {"password": "Passw0rd!23", "phrase": "yes"})
    assert missing_phrase.status_code == 400
    with app.app_context():
        assert db.session.get(User, user.id) is not None
