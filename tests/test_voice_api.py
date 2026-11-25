"""Functional tests for the /voice endpoints (JSON contract and happy paths)."""
from models import Document, Note, ShareLink, VoiceContext, db
from encryption import decrypt_note


def test_state_requires_login_and_answers_json(app):
    client = app.test_client()
    response = client.get("/voice/state")
    assert response.status_code == 401
    body = response.get_json()
    assert body["status"] == "error"
    assert "sign in" in body["message"].lower()


def test_state_reports_language_and_no_pending(login_as):
    vault, _user = login_as()
    response = vault.get("/voice/state")
    assert response.status_code == 200
    body = response.get_json()
    assert body["language"] == "en-US"
    assert body["pending"] is None


def test_command_requires_json(login_as):
    vault, _user = login_as()
    response = vault.post_form("/voice/command", {"text": "help"})
    assert response.status_code == 400
    assert response.get_json()["message"] == "Send JSON."


def test_command_rejects_empty_text(login_as):
    vault, _user = login_as()
    response = vault.voice("   ")
    assert response.status_code == 400
    assert "No speech text" in response.get_json()["message"]


def test_help_command(login_as):
    vault, _user = login_as()
    response = vault.voice("help")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert "confirm" in body["speak"].lower()
    assert body["intent"] == "help"


def test_unknown_command_is_reported(login_as):
    vault, _user = login_as()
    body = vault.voice("make me a sandwich").get_json()
    assert body["intent"] == "unknown"
    assert "help" in body["speak"].lower()


def test_list_documents(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_document(user.id, name="invoice.pdf", folder="Finance")
    body = vault.voice("list my files").get_json()
    assert body["intent"] == "list_documents"
    names = {doc["name"] for doc in body["data"]["documents"]}
    assert names == {"passport.pdf", "invoice.pdf"}
    assert "2 files" in body["speak"]


def test_search_documents(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_document(user.id, name="invoice.pdf", folder="Finance")
    body = vault.voice("find invoice").get_json()
    assert body["intent"] == "search_documents"
    assert [doc["name"] for doc in body["data"]["documents"]] == ["invoice.pdf"]


def test_open_document_returns_preview_action(login_as, make_document):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf")
    body = vault.voice("open passport.pdf").get_json()
    assert body["client_action"] == {"type": "open_preview", "doc_id": doc_id}


def test_open_unknown_file_suggests_real_names(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    body = vault.voice("open my file named passpart").get_json()
    assert body["intent"] == "open_document"
    assert "client_action" not in body
    assert body["data"]["suggestions"] == ["passport.pdf"]
    assert "passport.pdf" in body["speak"]


def test_open_folder_returns_navigation(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf", folder="Finance")
    body = vault.voice("open the Finance folder").get_json()
    assert body["client_action"]["type"] == "navigate"
    assert body["client_action"]["url"] == "/folders/Finance"


def test_open_unknown_folder_lists_folders(login_as):
    vault, _user = login_as()
    body = vault.voice("open the Nonsense folder").get_json()
    assert "couldn't find a folder" in body["speak"]


def test_list_folders(login_as):
    vault, _user = login_as()
    body = vault.voice("list my folders").get_json()
    assert "Identity" in body["data"]["folders"]


def test_list_contacts(login_as, make_contact):
    vault, user = login_as()
    make_contact(user.id, name="Rahul")
    body = vault.voice("list my saved contacts").get_json()
    assert body["data"]["contacts"][0]["name"] == "Rahul"


def test_list_notes_returns_titles_only(login_as, app):
    vault, user = login_as()
    with app.app_context():
        db.session.add(Note(user_id=user.id, title="Renewals", content="encrypted-blob"))
        db.session.commit()
    body = vault.voice("list my notes").get_json()
    assert body["data"]["notes"][0]["title"] == "Renewals"
    assert "encrypted-blob" not in str(body)


def test_folder_contents_question_lists_files(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf", folder="Finance")
    make_document(user.id, name="passport.pdf", folder="Identity")
    body = vault.voice("what's in my Finance folder").get_json()
    assert body["intent"] == "list_folder_contents"
    assert [doc["name"] for doc in body["data"]["documents"]] == ["invoice.pdf"]
    assert "Finance folder has 1 file" in body["speak"]


def test_where_is_file_reports_folder_and_date(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf", folder="Identity")
    body = vault.voice("where is my passport").get_json()
    assert body["intent"] == "document_info"
    assert "passport.pdf" in body["speak"]
    assert "Identity" in body["speak"]


def test_small_talk_is_answered(login_as):
    vault, _user = login_as()
    body = vault.voice("hello").get_json()
    assert body["intent"] == "small_talk"
    assert body["status"] == "ok"


def test_how_to_question_is_answered(login_as):
    vault, _user = login_as()
    body = vault.voice("how do I upload a file").get_json()
    assert body["intent"] == "faq"
    assert "Add files" in body["speak"]


def test_active_share_question_is_answered(login_as):
    vault, _user = login_as()
    body = vault.voice("what shares do I have").get_json()
    assert body["intent"] == "list_shares"
    assert "no active share links" in body["speak"].lower()


def test_open_trash_and_shares_actions(login_as):
    vault, _user = login_as()
    assert vault.voice("show the trash").get_json()["client_action"]["type"] == "open_trash"
    shares = vault.voice("open my share links").get_json()
    assert shares["client_action"]["url"] == "/shares"


def test_blocked_command_returns_blocked_status(login_as):
    vault, _user = login_as()
    response = vault.voice("delete all my files")
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "blocked"
    assert body["intent"] == "blocked"


def test_dictated_email_is_refused(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    body = vault.voice("share passport.pdf with rahul@example.com").get_json()
    assert body["status"] == "blocked"
    assert "saved contact" in body["speak"].lower()


def test_share_prepare_creates_pending_but_no_link(login_as, app, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    response = vault.voice("share passport.pdf with Rahul")
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["pending"]["nonce"]
    assert body["pending"]["expires_in"] > 0
    with app.app_context():
        assert ShareLink.query.filter_by(user_id=user.id).count() == 0


def test_share_confirm_creates_one_time_link(login_as, app, make_document, make_contact):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf")
    contact_id = make_contact(user.id, name="Rahul")
    pending = vault.voice("share passport.pdf with Rahul").get_json()["pending"]

    response = vault.post_json("/voice/confirm", {"nonce": pending["nonce"]})
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["data"]["share_url"].startswith("http")
    assert body["client_action"]["type"] == "share_link"

    with app.app_context():
        link = ShareLink.query.filter_by(user_id=user.id).one()
        assert link.document_id == doc_id
        assert link.max_uses == 1
        assert link.use_count == 0
        assert link.password_hash is None
        assert link.recipient_contact_id == contact_id
        assert link.recipient_email == "rahul@example.test"
        assert link.share_kind == "document"
        assert link.expires_at is not None
        # The stored token is a hash, never the URL token.
        assert len(link.token) == 64
        assert link.token not in body["data"]["share_url"]


def test_note_prepare_then_confirm_encrypts_content(login_as, app):
    vault, user = login_as()
    pending = vault.voice(
        "create a note titled Renewals with content passport expires in June"
    ).get_json()["pending"]
    assert pending["summary"] == 'Create a note titled "Renewals".'

    body = vault.post_json("/voice/confirm", {"nonce": pending["nonce"]}).get_json()
    assert body["status"] == "ok"
    assert "Renewals" in body["speak"]

    with app.app_context():
        note = Note.query.filter_by(user_id=user.id).one()
        assert note.title == "Renewals"
        assert "passport expires in June" not in note.content
        assert decrypt_note(note.content) == "passport expires in June"


def test_cancel_clears_the_pending_action(login_as, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    pending = vault.voice("share passport.pdf with Rahul").get_json()["pending"]

    cancel = vault.post_json("/voice/cancel", {"nonce": pending["nonce"]}).get_json()
    assert cancel["status"] == "ok"
    assert vault.get("/voice/state").get_json()["pending"] is None

    retry = vault.post_json("/voice/confirm", {"nonce": pending["nonce"]}).get_json()
    assert retry["status"] == "error"


def test_voice_confirm_without_pending_is_rejected(login_as):
    vault, _user = login_as()
    body = vault.post_json("/voice/confirm", {"nonce": "anything"}).get_json()
    assert body["status"] == "error"
    assert "no pending action" in body["speak"].lower()


def test_pending_state_survives_a_reload(login_as, make_document, make_contact):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    vault.voice("share passport.pdf with Rahul")
    state = vault.get("/voice/state").get_json()
    assert state["pending"]["nonce"]
    assert "Share" in state["pending"]["summary"]


def test_voice_responses_are_not_cacheable(login_as):
    vault, _user = login_as()
    response = vault.get("/voice/state")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
