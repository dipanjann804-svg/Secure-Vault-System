"""Unit tests for the deterministic command planner (no database, no browser)."""
import re

import voice_service
from voice import PREPARE_TOOLS, READ_TOOLS
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


def test_how_to_use_the_app_is_answered_locally():
    for question in (
        "how to use this app",
        "how does this app work",
        "how do I get started",
        "what can this app do",
    ):
        plan = parse_voice_command(question)
        assert plan["intent"] == "faq", question
        assert "dashboard" in plan["speak"].lower(), question


class _FakeClock:
    def __init__(self, hour, label):
        self.hour = hour
        self._label = label

    def strftime(self, _fmt):
        return self._label


def test_a_greeting_that_matches_the_clock_is_returned(monkeypatch):
    monkeypatch.setattr(voice_service, "_local_now", lambda: _FakeClock(8, "08:15 AM"))
    plan = parse_voice_command("good morning")
    assert plan["intent"] == "small_talk"
    assert plan["speak"].startswith("Good morning.")


def test_a_wrong_greeting_is_corrected_with_the_real_time(monkeypatch):
    monkeypatch.setattr(voice_service, "_local_now", lambda: _FakeClock(12, "12:05 PM"))
    plan = parse_voice_command("good morning")
    assert plan["intent"] == "small_talk"
    assert "12:05 PM" in plan["speak"]
    assert "good afternoon" in plan["speak"].lower()


def test_good_evening_said_in_the_morning_is_corrected(monkeypatch):
    monkeypatch.setattr(voice_service, "_local_now", lambda: _FakeClock(9, "09:00 AM"))
    plan = parse_voice_command("good evening")
    assert "good morning" in plan["speak"].lower()


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


def test_small_talk_gets_a_friendly_answer():
    for text in ("hello", "Hi there", "thank you", "how are you", "who are you", "okay"):
        plan = parse_voice_command(text)
        assert plan["intent"] == "small_talk", text
        assert plan["tier"] == TIER_READ, text
        assert plan["speak"], text


def test_greeting_and_check_in_together_are_small_talk():
    plan = parse_voice_command("Good morning. How are you?")
    assert plan["intent"] == "small_talk"
    assert plan["speak"].startswith("Good morning.")
    assert "ready" in plan["speak"].lower()


def test_small_talk_chain_answers_the_last_question():
    plan = parse_voice_command("thanks, and how are you doing today")
    assert plan["intent"] == "small_talk"
    assert "ready" in plan["speak"].lower()


def test_greeting_before_a_command_still_runs_the_command():
    plan = parse_voice_command("Good morning, open passport.pdf")
    assert plan["intent"] == "open_document"
    assert plan["document_query"] == "passport.pdf"


def test_how_to_questions_get_a_direct_answer():
    for text in (
        "how do I upload a file",
        "how can I share a file",
        "how do I add a contact",
        "how do I restore a deleted file",
        "is my vault secure",
        "how do I create a note",
        "where do I download my file",
        "how do I change my password",
    ):
        plan = parse_voice_command(text)
        assert plan["intent"] == "faq", text
        assert plan["tier"] == TIER_READ, text


def test_password_how_to_explains_the_interface():
    plan = parse_voice_command("how do I change my password")
    assert plan["intent"] == "faq"
    assert "Change password" in plan["speak"]


def test_account_deletion_how_to_explains_the_interface():
    plan = parse_voice_command("how do I delete my account")
    assert plan["intent"] == "faq"
    assert "Settings" in plan["speak"]


def test_vault_questions_map_to_read_only_answers():
    cases = {
        "what files do I have": "list_documents",
        "show my recent files": "list_documents",
        "which folders do I have": "list_folders",
        "what notes do I have": "list_notes",
        "who are my contacts": "list_contacts",
        "what shares do I have": "list_shares",
        "what's in my Finance folder": "list_folder_contents",
        "where is my passport": "document_info",
        "which folder is passport in": "document_info",
        "when did I upload passport": "document_info",
        "how big is passport": "document_info",
        "what did I add recently": "list_documents",
        "do I have a file called passport": "search_documents",
        "where is the Finance folder": "open_folder",
    }
    for text, intent in cases.items():
        plan = parse_voice_command(text)
        assert plan["intent"] == intent, text
        assert plan["tier"] == TIER_READ, text


def test_folder_contents_extracts_the_folder_name():
    assert parse_voice_command("what's in my Finance folder")["folder_query"] == "Finance"


def test_document_details_extract_the_file_name():
    assert parse_voice_command("where is my passport")["document_query"] == "passport"


def test_reading_a_note_aloud_is_blocked():
    plan = parse_voice_command("read my note titled Passwords")
    assert plan["intent"] == "blocked"
    assert plan["blocked_reason"] == "note_content"


def test_unmatched_question_falls_back_to_its_topic():
    plan = parse_voice_command("the folder situation")
    assert plan["intent"] == "list_folders"
    assert plan["tier"] == TIER_READ
    assert parse_voice_command("make me a sandwich")["intent"] == "unknown"


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


def test_arithmetic_is_computed_not_guessed():
    plan = parse_voice_command("what is 12 times 7")
    assert plan["intent"] == "general_answer"
    assert "84" in plan["speak"]


def test_arithmetic_respects_precedence():
    assert "14" in parse_voice_command("what is 2 plus 3 times 4")["speak"]


def test_division_by_zero_is_explained():
    plan = parse_voice_command("what is 5 divided by 0")
    assert plan["intent"] == "general_answer"
    assert "zero" in plan["speak"].lower()


def test_time_question_is_answered_locally():
    plan = parse_voice_command("what time is it")
    assert plan["intent"] == "general_answer"
    assert re.search(r"\d", plan["speak"])


def test_outside_world_questions_explain_the_limit():
    for text in (
        "what's the weather today",
        "who won the match",
        "translate hello to Spanish",
        "what is the capital of France",
    ):
        plan = parse_voice_command(text)
        assert plan["intent"] == "limit", text
        assert "can't" in plan["speak"].lower(), text


def test_vague_reference_asks_a_clarifying_question():
    plan = parse_voice_command("the file")
    assert plan["intent"] == "clarify"
    assert "which file" in plan["speak"].lower()


def test_bare_search_asks_for_a_term():
    plan = parse_voice_command("search")
    assert plan["intent"] == "clarify"
    assert "?" in plan["speak"]


def test_every_transcript_gets_a_spoken_answer():
    # The point of the ladder: no input leaves the voice interface silent.
    for text in (
        "", "   ", "asdfgh qwerty", "make me a sandwich", "???", "please",
        "\U0001f600", "the", "open", "it", "what", "do the thing",
    ):
        plan = parse_voice_command(text)
        assert isinstance(plan.get("speak"), str), text
        assert plan["speak"].strip(), text


def _all_tools():
    groups = (
        voice_service._SMALL_TALK_TOOLS,
        voice_service._COMMAND_TOOLS,
        voice_service._NAV_TOOLS,
        voice_service._ANSWER_TOOLS,
        voice_service._READ_TOOLS,
        voice_service._CLARIFY_TOOLS,
        voice_service._LIMIT_TOOLS,
        voice_service._GENERAL_TOOLS,
        voice_service._FALLBACK_TOOLS,
    )
    return [tool for group in groups for tool in group]


def test_every_registered_tool_is_well_formed():
    for tool in _all_tools():
        assert tool.name and tool.audit and tool.speech, tool
        assert tool.patterns, tool.name
        for pattern in tool.patterns:
            re.compile(pattern)


def test_every_tool_name_has_a_handler():
    # A tool without a handler would silently answer with its generic speech,
    # so the registry and the handler table must stay in step.
    names = {tool.name for tool in _all_tools()}
    handled = set(READ_TOOLS) | set(PREPARE_TOOLS) | {"confirm", "cancel"}
    assert names <= handled


def test_adding_a_phrasing_needs_no_new_tool():
    # One tool owns many ways to ask; this guards against the registry growing
    # back into one entry per sentence.
    search = next(tool for tool in voice_service._READ_TOOLS if tool.name == "search_documents")
    assert len(search.patterns) >= 1
    for text in ("find passport", "search for invoice", "look for bills", "do i have a lease"):
        plan = parse_voice_command(text)
        assert plan["intent"] == "search_documents", text


def test_transcript_is_capped_before_parsing():
    # The planner never sees more than VOICE_MAX_TRANSCRIPT characters, so a
    # runaway dictation cannot create an unbounded note.
    plan = parse_voice_command("create a note titled X with content " + "a" * 5000)
    assert plan["intent"] == "create_note"
    assert len(plan["note_content"]) <= VOICE_MAX_TRANSCRIPT
