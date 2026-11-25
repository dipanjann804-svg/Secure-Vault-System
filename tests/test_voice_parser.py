"""Unit tests for the deterministic command planner (no database, no browser)."""
from voice_service import (
    TIER_BLOCKED,
    TIER_CONFIRM,
    TIER_READ,
    TIER_SENSITIVE,
    VOICE_LANGUAGE,
    VOICE_MAX_TRANSCRIPT,
    normalize_transcript,
    parse_voice_command,
)


def test_language_is_english_us():
    assert VOICE_LANGUAGE == "en-US"


def test_normalize_collapses_whitespace_and_caps_length():
    assert normalize_transcript("  list   my\n files  ") == "list my files"
    assert len(normalize_transcript("a" * 5000)) == VOICE_MAX_TRANSCRIPT


def test_empty_transcript():
    plan = parse_voice_command("   ")
    assert plan["intent"] == "empty"
    assert plan["tier"] == TIER_READ


def test_help_is_read_only():
    plan = parse_voice_command("help")
    assert plan["intent"] == "help"
    assert plan["tier"] == TIER_READ


def test_list_documents():
    plan = parse_voice_command("list my files")
    assert plan["intent"] == "list_documents"
    assert plan["tier"] == TIER_READ


def test_vault_summary():
    assert parse_voice_command("what's in my vault")["intent"] == "vault_summary"


def test_search_extracts_query():
    plan = parse_voice_command("find passport")
    assert plan["intent"] == "search_documents"
    assert plan["query"] == "passport"


def test_open_document_extracts_name():
    plan = parse_voice_command("open passport.pdf")
    assert plan["intent"] == "open_document"
    assert plan["document_query"] == "passport.pdf"


def test_open_folder_extracts_name():
    plan = parse_voice_command("open the Finance folder")
    assert plan["intent"] == "open_folder"
    assert plan["folder_query"] == "Finance"


def test_polite_phrasing_still_opens_the_named_file():
    for text in (
        "Can you please open my file named passport.pdf?",
        "I want you to open my passport.pdf file. Do it.",
        "Hey, could you please open passport.pdf",
        "open my file name passport.pdf",
    ):
        plan = parse_voice_command(text)
        assert plan["intent"] == "open_document", text
        assert plan["document_query"] == "passport.pdf", text


def test_open_my_folder_asks_which_folder():
    plan = parse_voice_command("Can you open my folder please")
    assert plan["intent"] == "open_folder"
    assert plan["folder_query"] == ""


def test_polite_confirmation_still_confirms():
    assert parse_voice_command("OK, do it")["intent"] == "confirm"
    assert parse_voice_command("yes please")["intent"] == "confirm"


def test_open_trash_is_allowed_but_delete_is_blocked():
    assert parse_voice_command("show the trash")["intent"] == "open_trash"
    blocked = parse_voice_command("empty the trash")
    assert blocked["intent"] == "blocked"
    assert blocked["tier"] == TIER_BLOCKED


def test_list_contacts():
    assert parse_voice_command("list my saved contacts")["intent"] == "list_contacts"


def test_confirm_and_cancel_keywords():
    assert parse_voice_command("confirm")["intent"] == "confirm"
    assert parse_voice_command("yes")["intent"] == "confirm"
    assert parse_voice_command("cancel")["intent"] == "cancel"
    assert parse_voice_command("never mind")["intent"] == "cancel"


def test_unknown_command():
    plan = parse_voice_command("make me a sandwich")
    assert plan["intent"] == "unknown"


def test_destructive_commands_are_blocked():
    for text in (
        "delete all my files",
        "remove the passport file",
        "purge my notes",
        "erase my account",
    ):
        plan = parse_voice_command(text)
        assert plan["intent"] == "blocked", text
        assert plan["tier"] == TIER_BLOCKED


def test_authentication_and_upload_commands_are_blocked():
    for text in (
        "change my password",
        "reset the password",
        "upload a new file",
        "attach the document",
    ):
        assert parse_voice_command(text)["intent"] == "blocked", text


def test_reveal_secret_is_blocked():
    assert parse_voice_command("show my password")["intent"] == "blocked"


def test_outbound_email_is_blocked():
    assert parse_voice_command("send an email to Rahul")["intent"] == "blocked"


def test_dictated_email_address_is_rejected():
    plan = parse_voice_command("share passport.pdf with rahul@example.com")
    assert plan["intent"] == "blocked"
    assert plan["blocked_reason"] == "dictated_email"


def test_share_with_saved_contact_defaults_to_once_and_one_hour():
    plan = parse_voice_command("share passport.pdf with Rahul")
    assert plan["intent"] == "share_document"
    assert plan["tier"] == TIER_SENSITIVE
    assert plan["document_query"] == "passport.pdf"
    assert plan["contact_query"] == "Rahul"
    assert plan["usage"] == "once"
    assert plan["expiry"] == "1h"


def test_share_parses_usage_and_expiry_options():
    plan = parse_voice_command(
        "share passport.pdf with Rahul multiple times for 10 minutes"
    )
    assert plan["contact_query"] == "Rahul"
    assert plan["usage"] == "multi"
    assert plan["expiry"] == "10m"


def test_share_rejects_unsupported_expiry():
    plan = parse_voice_command("share passport.pdf with Rahul for a week")
    assert plan["intent"] == "error"
    assert "10 minutes" in plan["speak"]


def test_share_without_recipient_asks_for_one():
    plan = parse_voice_command("share passport.pdf")
    assert plan["intent"] == "error"


def test_create_note_extracts_title_and_content():
    plan = parse_voice_command(
        "create a note titled Renewal dates with content passport expires in June"
    )
    assert plan["intent"] == "create_note"
    assert plan["tier"] == TIER_CONFIRM
    assert plan["note_title"] == "Renewal dates"
    assert plan["note_content"] == "passport expires in June"


def test_create_note_without_body_asks_for_one():
    assert parse_voice_command("create a note")["intent"] == "ask_note"


def test_transcript_is_capped_before_parsing():
    # The planner never sees more than VOICE_MAX_TRANSCRIPT characters, so a
    # runaway dictation cannot create an unbounded note.
    plan = parse_voice_command("create a note titled X with content " + "a" * 5000)
    assert plan["intent"] == "create_note"
    assert len(plan["note_content"]) <= VOICE_MAX_TRANSCRIPT
