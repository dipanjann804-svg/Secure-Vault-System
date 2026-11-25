"""Security-focused tests: pending actions, isolation, CSRF, auditing, limits."""
import json
from datetime import datetime, timedelta

import pytest

import app as app_module
from encryption import decrypt_note
from models import Contact, Document, Note, SecurityLog, ShareLink, VoiceContext, db
from voice_service import VOICE_RATE_LIMIT


def _pending_nonce(app, user_id):
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user_id).first()
        return ctx.pending_nonce if ctx else None


def _link_count(app, user_id):
    with app.app_context():
        return ShareLink.query.filter_by(user_id=user_id).count()


# --- CSRF -----------------------------------------------------------------
@pytest.mark.parametrize("path", ["/voice/command", "/voice/confirm", "/voice/cancel"])
def test_voice_posts_require_csrf(login_as, path):
    vault, _user = login_as()
    response = vault.post_json(path, {"text": "help"}, csrf=False)
    assert response.status_code == 400
    body = response.get_json()
    assert body["status"] == "error"
    assert "token" in body["message"].lower()


def test_voice_get_is_unaffected_by_csrf(login_as):
    vault, _user = login_as()
    assert vault.get("/voice/state").status_code == 200


# --- Pending action confidentiality ---------------------------------------
def test_pending_payload_is_encrypted_at_rest(login_as, app):
    vault, user = login_as()
    secret = "my safe code is 4455"
    vault.voice(f"create a note titled Renewals with content {secret}")
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).one()
        assert ctx.pending_action
        assert "4455" not in ctx.pending_action
        assert "safe code" not in ctx.pending_action
        payload = json.loads(decrypt_note(ctx.pending_action))
        assert payload["kind"] == "create_note"
        assert payload["content"] == secret


def test_pending_nonce_is_long_and_random(login_as, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    first = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]
    second = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]
    assert first != second
    assert len(first) >= 20


def test_note_content_is_never_spoken_or_displayed(login_as, app):
    vault, user = login_as()
    secret = "the passport expires in June"
    body = vault.voice(f"create a note titled Renewals with content {secret}").get_json()
    assert secret not in body["speak"]
    assert secret not in body["display"]
    assert secret not in json.dumps(body.get("pending", {}))
    assert secret not in json.dumps(body.get("data", {}))

    confirmed = vault.post_json("/voice/confirm", {"nonce": body["pending"]["nonce"]}).get_json()
    assert secret not in json.dumps(confirmed)


def test_note_content_never_reaches_the_security_log(login_as, app):
    vault, user = login_as()
    secret = "vault code 7781"
    body = vault.voice(f"create a note titled Renewals with content {secret}").get_json()
    vault.post_json("/voice/confirm", {"nonce": body["pending"]["nonce"]})
    with app.app_context():
        rows = SecurityLog.query.filter_by(user_id=user.id).all()
        assert rows
        blob = json.dumps([
            {"tool": row.tool, "outcome": row.outcome, "login_id": row.login_id}
            for row in rows
        ])
        assert secret not in blob
        assert "Renewals" not in blob


# --- Nonce / replay / expiry ----------------------------------------------
def test_wrong_nonce_is_rejected_and_pending_is_kept(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    body = vault.post_json("/voice/confirm", {"nonce": "not-the-nonce"}).get_json()
    assert body["status"] == "error"
    assert _link_count(app, user.id) == 0
    assert _pending_nonce(app, user.id) == nonce

    ok = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert ok["status"] == "ok"
    assert _link_count(app, user.id) == 1


def test_confirmation_cannot_be_replayed(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    first = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    second = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert first["status"] == "ok"
    assert second["status"] == "error"
    assert _link_count(app, user.id) == 1


def test_voice_confirm_command_cannot_be_replayed(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    assert vault.voice("confirm", nonce=nonce).get_json()["status"] == "ok"
    assert vault.voice("confirm", nonce=nonce).get_json()["status"] == "error"
    assert _link_count(app, user.id) == 1


def test_expired_pending_action_is_refused(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).one()
        ctx.pending_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

    body = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert body["status"] == "error"
    assert "expired" in body["speak"].lower()
    assert _link_count(app, user.id) == 0


def test_expired_pending_is_cleared_from_state(login_as, app):
    vault, user = login_as()
    vault.voice("create a note titled Temp with content temporary text")
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).one()
        ctx.pending_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert vault.get("/voice/state").get_json()["pending"] is None


def test_cancel_without_nonce_clears_pending(login_as, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    vault.voice("share passport.pdf with Rahul")
    assert vault.voice("cancel").get_json()["status"] == "ok"
    assert vault.get("/voice/state").get_json()["pending"] is None


# --- Owner isolation ------------------------------------------------------
def test_nonce_cannot_be_confirmed_by_another_account(app, login_as, make_document, make_contact):
    vault_a, user_a = login_as()
    make_document(user_a.id, name="passport.pdf")
    make_contact(user_a.id, name="Rahul")
    nonce = vault_a.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    vault_b, user_b = login_as()
    stolen = vault_b.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert stolen["status"] == "error"
    assert _link_count(app, user_b.id) == 0
    # The rightful owner's pending action is untouched.
    assert _pending_nonce(app, user_a.id) == nonce
    assert vault_a.post_json("/voice/confirm", {"nonce": nonce}).get_json()["status"] == "ok"


def test_documents_are_owner_scoped(login_as, make_document):
    owner_a, user_a = login_as()
    make_document(user_a.id, name="passport.pdf")

    owner_b, _user_b = login_as()
    body = owner_b.voice("find passport").get_json()
    assert body["data"]["documents"] == []
    opened = owner_b.voice("open passport.pdf").get_json()
    assert "client_action" not in opened


def test_contacts_are_owner_scoped(app, login_as, make_document, make_contact):
    owner_a, user_a = login_as()
    make_document(user_a.id, name="passport.pdf")
    make_contact(user_a.id, name="Rahul")

    owner_b, _user_b = login_as()
    body = owner_b.voice("list my saved contacts").get_json()
    assert "do not have any saved contacts" in body["speak"]
    assert "data" not in body


# --- Revalidation at confirmation time ------------------------------------
def test_share_revalidates_document_ownership_and_state(login_as, app, make_document, make_contact):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    with app.app_context():
        doc = db.session.get(Document, doc_id)
        doc.deleted_at = datetime.utcnow()
        db.session.commit()

    body = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert body["status"] == "error"
    assert "no longer available" in body["speak"].lower()
    assert _link_count(app, user.id) == 0


def test_share_revalidates_contact_ownership(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    contact_id = make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]

    with app.app_context():
        db.session.delete(db.session.get(Contact, contact_id))
        db.session.commit()

    body = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()
    assert body["status"] == "error"
    assert _link_count(app, user.id) == 0


def test_share_never_sends_email(login_as, app, monkeypatch, make_document, make_contact):
    def _forbidden(*args, **kwargs):
        raise AssertionError("voice sharing must never send email")

    monkeypatch.setattr(app_module, "send_share_email", _forbidden)

    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]
    body = vault.post_json("/voice/confirm", {"nonce": nonce}).get_json()

    assert body["status"] == "ok"
    assert body["data"]["email_sent"] is False
    assert "never sends email" in body["speak"]
    with app.app_context():
        link = ShareLink.query.filter_by(user_id=user.id).one()
        assert link.password_hash is None


def test_voice_share_never_sets_a_password(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    # Even if the transcript mentions a password, it is not part of the plan.
    body = vault.voice("share passport.pdf with Rahul with password hunter2").get_json()
    assert "password" not in json.dumps(body.get("data", {}))
    if body.get("pending"):
        with app.app_context():
            ctx = VoiceContext.query.filter_by(user_id=user.id).one()
            assert "hunter2" not in ctx.pending_action


# --- Rate limiting --------------------------------------------------------
def test_rate_limit_blocks_further_commands(login_as, app):
    vault, user = login_as()
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).first()
        if ctx is None:
            ctx = VoiceContext(user_id=user.id)
            db.session.add(ctx)
        ctx.window_started_at = datetime.utcnow()
        ctx.command_count = VOICE_RATE_LIMIT
        db.session.commit()

    response = vault.voice("help")
    assert response.status_code == 429
    assert response.get_json()["status"] == "error"


def test_rate_limit_applies_to_confirm(login_as, app):
    vault, user = login_as()
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).first()
        if ctx is None:
            ctx = VoiceContext(user_id=user.id)
            db.session.add(ctx)
        ctx.window_started_at = datetime.utcnow()
        ctx.command_count = VOICE_RATE_LIMIT
        db.session.commit()
    response = vault.post_json("/voice/confirm", {"nonce": "x"})
    assert response.status_code == 429


def test_one_voice_context_row_per_user(login_as, app):
    vault, user = login_as()
    vault.voice("help")
    vault.voice("list my files")
    vault.get("/voice/state")
    with app.app_context():
        assert VoiceContext.query.filter_by(user_id=user.id).count() == 1


# --- Auditing -------------------------------------------------------------
def test_blocked_command_is_audited(login_as, app):
    vault, user = login_as()
    vault.voice("delete all my files")
    with app.app_context():
        row = SecurityLog.query.filter_by(user_id=user.id, tool="voice_blocked").first()
        assert row is not None
        assert row.outcome == "blocked"
        assert row.status == "FAILED"


def test_successful_share_is_audited(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    nonce = vault.voice("share passport.pdf with Rahul").get_json()["pending"]["nonce"]
    vault.post_json("/voice/confirm", {"nonce": nonce})
    with app.app_context():
        tools = {
            row.tool
            for row in SecurityLog.query.filter_by(user_id=user.id).all()
        }
        assert "voice_share_prepare" in tools
        assert "voice_share" in tools


def test_read_only_commands_are_audited(login_as, app):
    vault, user = login_as()
    vault.voice("list my files")
    with app.app_context():
        row = SecurityLog.query.filter_by(user_id=user.id, tool="voice_list_documents").first()
        assert row is not None
        assert row.outcome == "ok"
