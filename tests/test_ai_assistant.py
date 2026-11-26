"""Tests for the Gemini-powered conversational layer.

No network access and no real API key: every provider call is a fake that
returns canned JSON, so these tests describe the *contract* the backend
enforces on model output.
"""
import json

import pytest

from ai import conversation_manager, gemini_provider, knowledge_retriever, llm_service
from ai import response_parser, schemas
from ai.provider_base import LLMProvider, LLMResult
from models import Document, ShareLink, VoiceContext, db


class FakeProvider(LLMProvider):
    """Records prompts and replays canned payloads."""

    name = "fake"
    model = "fake-model"

    def __init__(self, payloads=None, error_code="", detail=""):
        self.payloads = list(payloads or [])
        self.error_code = error_code
        self.detail = detail
        self.calls = []

    def available(self):
        return True

    def generate(self, *, system_instruction, user_prompt, response_schema=None, temperature=0.4):
        self.calls.append(
            {
                "system": system_instruction,
                "prompt": user_prompt,
                "schema": response_schema,
            }
        )
        if self.error_code:
            return LLMResult(False, error_code=self.error_code, error_detail=self.detail, latency_ms=5)
        payload = self.payloads.pop(0) if self.payloads else {}
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return LLMResult(True, text=text, latency_ms=7)


@pytest.fixture()
def install_ai(monkeypatch):
    """Install a fake provider for the duration of one test."""

    def _install(payloads=None, error_code="", detail="", **overrides):
        provider = FakeProvider(payloads, error_code, detail)
        settings = llm_service.AISettings(
            enabled=overrides.pop("enabled", True),
            api_key=overrides.pop("api_key", "test-key-not-real"),
            model=overrides.pop("model", "fake-model"),
            timeout=overrides.pop("timeout", 20.0),
            max_retries=overrides.pop("max_retries", 2),
            metadata=overrides.pop("metadata", "names"),
            history_turns=overrides.pop("history_turns", 6),
        )
        service = llm_service.LLMService(provider, settings)
        monkeypatch.setattr(llm_service, "get_llm_service", lambda: service)
        return provider

    return _install


def _answer(category, answer, speech=None):
    return {
        "category": category,
        "answer": answer,
        "speech": speech or answer,
        "risk_level": "READ_ONLY",
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# General conversation and technical explanation
# ---------------------------------------------------------------------------
def test_general_question_is_answered_by_the_model(login_as, install_ai):
    provider = install_ai([
        _answer(
            "GENERAL_KNOWLEDGE",
            "A short interesting fact: octopuses have three hearts.",
            "Octopuses have three hearts.",
        )
    ])
    vault, _user = login_as()
    body = vault.voice("tell me something interesting").get_json()

    assert body["status"] == "ok"
    assert body["request_category"] == "GENERAL_KNOWLEDGE"
    assert body["speak"] == "Octopuses have three hearts."
    assert body["display"].startswith("A short interesting fact")
    assert body["response_text"].startswith("A short interesting fact")
    assert body["ai"]["state"] == "ok"
    assert body["ai"]["model"] == "fake-model"
    assert provider.calls, "the model should have been called"


def test_app_usage_question_goes_to_the_model_not_the_local_faq(login_as, install_ai):
    # The local getting-started answer is only a fallback for when the model is
    # unavailable; with a provider configured the conversational brain answers.
    provider = install_ai([
        _answer(
            "VAULT_HELP",
            "Sign in, then use the dashboard: Add files uploads a document.",
        )
    ])
    vault, _user = login_as()
    body = vault.voice("how to use this app").get_json()

    assert provider.calls, "the model should be consulted, not the local FAQ"
    assert body["ai"]["state"] == "ok"
    assert body["request_category"] == "VAULT_HELP"
    assert "Add files" in body["display"]


def test_technical_question_gets_a_model_explanation(login_as, install_ai):
    install_ai([
        _answer(
            "TECHNICAL_KNOWLEDGE",
            "Quantum computing uses qubits that can hold superpositions.",
            "Qubits can be in superposition, so certain problems run faster.",
        )
    ])
    vault, _user = login_as()
    body = vault.voice("what is quantum computing").get_json()

    assert body["status"] == "ok"
    assert body["request_category"] == "TECHNICAL_KNOWLEDGE"
    assert "qubit" in body["display"].lower()


def test_greeting_can_be_answered_conversationally(login_as, install_ai):
    install_ai([_answer("GENERAL_KNOWLEDGE", "Hello! How can I help with your vault today?")])
    vault, _user = login_as()
    body = vault.voice("hello there, how are you").get_json()
    assert body["status"] == "ok"
    assert body["speak"].strip()


# ---------------------------------------------------------------------------
# Vault tools proposed by the model run through the existing handlers
# ---------------------------------------------------------------------------
def test_model_selected_search_uses_the_existing_handler(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf", folder="Finance")
    make_document(user.id, name="passport.pdf", folder="Identity")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "search_files",
            "parameters": {"query": "invoice"},
            "risk_level": "READ_ONLY",
            "confidence": 0.95,
        }
    ])
    body = vault.voice("which of my files is the invoice one").get_json()

    assert body["status"] == "ok"
    assert body["intent"] == "search_documents"
    assert body["tool_name"] == "search_files"
    assert body["request_category"] == "VAULT_TOOL"
    assert [doc["name"] for doc in body["tool_result"]["documents"]] == ["invoice.pdf"]


def test_follow_up_reference_is_routed_as_a_tool(login_as, make_document, install_ai):
    vault, user = login_as()
    doc_id = make_document(user.id, name="passport.pdf")
    install_ai([
        {
            "category": "FOLLOW_UP",
            "tool_name": "open_file",
            "parameters": {"query": "passport.pdf"},
            "risk_level": "READ_ONLY",
        }
    ])
    body = vault.voice("open the first one").get_json()
    assert body["client_action"] == {"type": "open_preview", "doc_id": doc_id}
    assert body["request_category"] == "FOLLOW_UP"


def test_model_cannot_use_a_tool_outside_the_catalogue(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "delete_everything",
            "parameters": {},
            "risk_level": "READ_ONLY",
        }
    ])
    body = vault.voice("archive the invoice please").get_json()

    assert body["ai"]["error_code"] == "unknown_tool"
    # The deterministic planner answered instead of executing anything.
    assert body["intent"] != "delete_everything"
    with vault.app.app_context():
        assert Document.query.filter_by(user_id=user.id, deleted_at=None).count() == 1


def test_injected_parameters_are_rejected(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "open_file",
            "parameters": {"query": "passport.pdf", "user_id": 999},
            "risk_level": "READ_ONLY",
        }
    ])
    body = vault.voice("show me my passport again")
    assert body.get_json()["ai"]["error_code"] == "invalid_parameters"


def test_model_cannot_force_a_mutation_to_execute_immediately(
    login_as, make_document, make_contact, install_ai
):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "share_file",
            "parameters": {"file": "passport.pdf", "contact": "Rahul", "expiry": "1h", "usage": "once"},
            # A model claiming READ_ONLY must not skip the confirmation.
            "risk_level": "READ_ONLY",
            "confidence": 0.99,
        }
    ])
    body = vault.voice("give Rahul access to my passport").get_json()

    assert body["requires_confirmation"] is True
    assert body["pending"]["nonce"]
    assert body["tool_name"] == "share_file"
    with vault.app.app_context():
        assert ShareLink.query.filter_by(user_id=user.id).count() == 0


def test_model_share_then_confirm_creates_the_link(
    login_as, make_document, make_contact, install_ai
):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    make_contact(user.id, name="Rahul")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "share_file",
            "parameters": {"file": "passport.pdf", "contact": "Rahul"},
            "risk_level": "MUTATION",
        }
    ])
    pending = vault.voice("send my passport to Rahul").get_json()["pending"]
    body = vault.post_json("/voice/confirm", {"nonce": pending["nonce"]}).get_json()

    assert body["status"] == "ok"
    assert body["data"]["share_url"].startswith("http")
    with vault.app.app_context():
        link = ShareLink.query.filter_by(user_id=user.id).one()
        assert link.max_uses == 1
        assert link.password_hash is None


def test_model_contact_email_in_parameters_is_not_accepted(
    login_as, make_document, install_ai
):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "share_file",
            "parameters": {"file": "passport.pdf", "contact": "rahul@example.com"},
            "risk_level": "MUTATION",
        }
    ])
    body = vault.voice("share my passport with rahul@example.com")
    # The dictated address is refused before the model is even called.
    assert body.get_json()["status"] == "blocked"


def test_model_declared_forbidden_action_is_refused(login_as, install_ai):
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "list_files",
            "parameters": {},
            "risk_level": "FORBIDDEN",
        }
    ])
    vault, _user = login_as()
    body = vault.voice("show me everything you can see").get_json()
    assert body["status"] == "blocked"
    assert body["request_category"] == "REFUSED"


# ---------------------------------------------------------------------------
# Knowledge, clarification, live information
# ---------------------------------------------------------------------------
def test_vault_help_is_grounded_in_local_documentation(login_as, install_ai):
    provider = install_ai([
        _answer("VAULT_HELP", "Notes are encrypted with Fernet before storage.", "Notes are encrypted before storage.")
    ])
    vault, _user = login_as()
    body = vault.voice("how are my notes protected").get_json()

    assert body["request_category"] == "VAULT_HELP"
    prompt = provider.calls[0]["prompt"]
    assert "REFERENCE DOCUMENTATION" in prompt
    assert "Fernet" in prompt


def test_current_information_is_never_fabricated(login_as, install_ai):
    install_ai([
        {
            "category": "CURRENT_INFORMATION",
            "answer": "It is 25 degrees and sunny in London.",
            "speech": "It is 25 degrees and sunny.",
            "risk_level": "READ_ONLY",
        }
    ])
    vault, _user = login_as()
    body = vault.voice("what is the weather in London right now").get_json()

    assert body["request_category"] == "CURRENT_INFORMATION"
    assert "25" not in body["speak"]
    assert "live information" in body["speak"].lower()


def test_ambiguous_request_gets_a_clarification(login_as, install_ai):
    install_ai([
        {
            "category": "CLARIFICATION",
            "clarification_question": "Which file do you mean: passport.pdf or invoice.pdf?",
            "risk_level": "READ_ONLY",
        }
    ])
    vault, _user = login_as()
    body = vault.voice("open that one from before").get_json()

    assert body["intent"] == "clarify"
    assert body["clarification_question"].startswith("Which file")
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Failure handling: the ladder must still answer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error_code",
    ["timeout", "rate_limited", "invalid_api_key", "provider_unavailable", "empty_response"],
)
def test_provider_failures_fall_back_to_deterministic_answers(
    login_as, make_document, install_ai, error_code
):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    install_ai(error_code=error_code)
    body = vault.voice("list my files").get_json()

    assert body["status"] == "ok"
    assert body["intent"] == "list_documents"
    assert body["ai"]["state"] == "error"
    assert body["ai"]["error_code"] == error_code
    assert "passport.pdf" in body["speak"]


def test_malformed_json_falls_back(login_as, install_ai):
    install_ai(["this is not json at all"])
    vault, _user = login_as()
    body = vault.voice("list my files").get_json()
    assert body["status"] == "ok"
    assert body["ai"]["error_code"] == "malformed_json"
    assert body["speak"].strip()


def test_empty_model_answer_is_rejected(login_as, install_ai):
    install_ai([{"category": "GENERAL_KNOWLEDGE", "answer": "   ", "speech": ""}])
    vault, _user = login_as()
    body = vault.voice("explain something vague").get_json()
    assert body["ai"]["error_code"] == "empty_answer"
    assert body["status"] == "ok"


def test_unknown_category_is_rejected(login_as, install_ai):
    install_ai([{"category": "RUN_SHELL_COMMAND", "answer": "rm -rf /"}])
    vault, _user = login_as()
    body = vault.voice("do something unusual").get_json()
    assert body["ai"]["error_code"] == "unknown_category"
    assert "rm -rf" not in json.dumps(body)


def test_api_key_is_never_returned_in_a_response(login_as, install_ai):
    install_ai(error_code="invalid_api_key", detail="API key not valid: test-key-not-real")
    vault, _user = login_as()
    response = vault.voice("list my files")
    assert "test-key-not-real" not in response.get_data(as_text=True)


def test_state_reports_provider_status_without_credentials(login_as, install_ai):
    install_ai()
    vault, _user = login_as()
    body = vault.get("/voice/state").get_json()
    assert body["ai"]["state"] == "ready"
    assert body["ai"]["provider"] == "fake"
    assert "api_key" not in json.dumps(body["ai"]).lower()


def test_without_a_key_the_assistant_still_answers(login_as, make_document):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    state = vault.get("/voice/state").get_json()
    assert state["ai"]["state"] == "unconfigured"
    body = vault.voice("list my files").get_json()
    assert body["intent"] == "list_documents"
    assert body["ai"]["state"] == "disabled"


# ---------------------------------------------------------------------------
# Privacy boundaries around the prompt
# ---------------------------------------------------------------------------
def test_dictated_note_body_never_reaches_the_model(login_as, install_ai):
    provider = install_ai([_answer("GENERAL_KNOWLEDGE", "unused")])
    vault, _user = login_as()
    body = vault.voice(
        "create a note titled Renewals with content my passport expires in June"
    ).get_json()

    assert provider.calls == [], "a dictated note must not be sent to the model"
    assert body["requires_confirmation"] is True
    assert body["pending"]["summary"] == 'Create a note titled "Renewals".'


def test_spoken_password_is_refused_locally(login_as, install_ai):
    provider = install_ai([_answer("GENERAL_KNOWLEDGE", "unused")])
    vault, _user = login_as()
    body = vault.voice("my password is hunter2").get_json()
    assert body["status"] == "blocked"
    assert provider.calls == []


def test_metadata_mode_none_sends_no_vault_facts(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    provider = install_ai(
        [_answer("GENERAL_KNOWLEDGE", "Sure.")], metadata="none"
    )
    vault.voice("what can you tell me").get_json()
    prompt = provider.calls[0]["prompt"]
    assert "AUTHORISED VAULT METADATA" not in prompt
    assert "passport.pdf" not in prompt


def test_metadata_mode_counts_hides_file_names(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="passport.pdf")
    provider = install_ai([_answer("GENERAL_KNOWLEDGE", "Sure.")], metadata="counts")
    vault.voice("what can you tell me").get_json()
    prompt = provider.calls[0]["prompt"]
    assert "Live files: 1" in prompt
    assert "passport.pdf" not in prompt


def test_other_users_files_are_not_visible_through_the_ai_path(
    login_as, make_document, install_ai
):
    owner, owner_user = login_as()
    make_document(owner_user.id, name="passport.pdf")
    other, _other_user = login_as()
    install_ai([
        {
            "category": "VAULT_TOOL",
            "tool_name": "open_file",
            "parameters": {"query": "passport.pdf"},
            "risk_level": "READ_ONLY",
        }
    ])
    body = other.voice("open passport.pdf").get_json()
    assert "client_action" not in body
    assert "passport" not in json.dumps(body.get("tool_result") or {})


# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------
def test_history_is_included_for_a_follow_up(login_as, make_document, install_ai):
    vault, user = login_as()
    make_document(user.id, name="invoice.pdf", folder="Finance")
    vault.voice("find invoice")  # deterministic turn, stored in context

    provider = install_ai([
        {
            "category": "FOLLOW_UP",
            "tool_name": "open_file",
            "parameters": {"query": "invoice.pdf"},
            "risk_level": "READ_ONLY",
        }
    ])
    vault.voice("open the first one").get_json()
    assert "RECENT CONVERSATION" in provider.calls[0]["prompt"]
    assert "find invoice" in provider.calls[0]["prompt"]


def test_context_is_isolated_between_users(login_as, make_document, install_ai):
    first, first_user = login_as()
    make_document(first_user.id, name="invoice.pdf", folder="Finance")
    first.voice("find invoice")

    second, _second_user = login_as()
    provider = install_ai([_answer("GENERAL_KNOWLEDGE", "Hello.")])
    second.voice("hello").get_json()
    assert "find invoice" not in provider.calls[0]["prompt"]


def test_what_did_i_ask_earlier_without_a_model(login_as):
    vault, _user = login_as()
    vault.voice("list my folders")
    body = vault.voice("what did I ask earlier").get_json()
    assert body["intent"] == "general_answer"
    assert "list my folders" in body["speak"]


def test_what_did_i_ask_earlier_with_no_history(login_as):
    vault, _user = login_as()
    body = vault.voice("what did I ask earlier").get_json()
    assert body["status"] == "ok"
    assert "earlier question" in body["speak"]


def test_expired_turns_are_dropped(app):
    data = {
        "turns": [
            {"role": "user", "text": "old question", "ts": 1000.0},
            {"role": "user", "text": "new question", "ts": 2000.0},
        ]
    }
    turns = conversation_manager.read_turns(data, ttl_seconds=60, now=2050.0)
    assert [turn["text"] for turn in turns] == ["new question"]


def test_note_content_is_redacted_before_it_is_remembered():
    data = {}
    conversation_manager.append_turn(
        data,
        "user",
        "create a note titled Renewals with content passport expires in June",
        ttl_seconds=600,
    )
    assert "passport expires in June" not in data["turns"][0]["text"]
    assert "Renewals" in data["turns"][0]["text"]


def test_ordinal_resolution():
    assert conversation_manager.resolve_ordinal("open the second one") == 2
    assert conversation_manager.resolve_ordinal("open the last file") == -1
    assert conversation_manager.resolve_ordinal("open the file") is None


# ---------------------------------------------------------------------------
# Unit tests: parser, schema, provider classification, retrieval
# ---------------------------------------------------------------------------
def test_parser_accepts_fenced_json():
    payload, error = response_parser.parse_decision('```json\n{"category": "REFUSED"}\n```')
    assert error == ""
    assert payload["category"] == "REFUSED"


def test_parser_rejects_non_object_and_garbage():
    assert response_parser.parse_decision("[1, 2, 3]")[1] == "malformed_json"
    assert response_parser.parse_decision("")[1] == "empty_response"
    assert response_parser.parse_decision("no braces here")[1] == "malformed_json"


def test_schema_rejects_missing_clarification_question():
    decision, error = schemas.validate_decision({"category": "CLARIFICATION"})
    assert decision is None
    assert error == "missing_clarification"


def test_schema_rejects_over_long_parameters():
    decision, error = schemas.validate_decision({
        "category": "VAULT_TOOL",
        "tool_name": "search_files",
        "parameters": {"query": "x" * 500},
    })
    assert decision is None
    assert error == "invalid_parameters"


def test_schema_requires_a_tool_for_a_vault_request():
    decision, error = schemas.validate_decision({"category": "VAULT_TOOL"})
    assert decision is None
    assert error == "unknown_tool"


def test_schema_ignores_model_risk_for_mutation_tools():
    decision, error = schemas.validate_decision({
        "category": "VAULT_TOOL",
        "tool_name": "create_note",
        "parameters": {"title": "T", "content": "C"},
        "risk_level": "READ_ONLY",
    })
    assert error == ""
    assert decision.requires_confirmation is True
    assert decision.risk_level == "MUTATION"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (TimeoutError("request timed out"), "timeout"),
        (type("APIError", (Exception,), {"code": 429})("quota"), "rate_limited"),
        (type("APIError", (Exception,), {"code": 401})("unauthenticated"), "invalid_api_key"),
        (type("APIError", (Exception,), {"code": 503})("unavailable"), "provider_unavailable"),
        (type("APIError", (Exception,), {"code": 404})("model not found"), "model_unavailable"),
        (RuntimeError("something odd"), "provider_error"),
    ],
)
def test_gemini_exception_classification(exc, expected):
    assert gemini_provider.classify_exception(exc) == expected


def test_provider_error_detail_redacts_the_key():
    detail = gemini_provider._safe_detail(
        RuntimeError("bad key sk-secret-123 rejected"), "sk-secret-123"
    )
    assert "sk-secret-123" not in detail
    assert "[redacted]" in detail


def test_gemini_provider_without_a_key_is_unavailable():
    provider = gemini_provider.GeminiProvider(api_key="")
    assert provider.available() is False
    result = provider.generate(system_instruction="s", user_prompt="p")
    assert result.ok is False
    assert result.error_code == "missing_api_key"


def test_retrieval_finds_relevant_documentation():
    chunks = knowledge_retriever.retrieve("how does sharing with a contact work")
    titles = [chunk.title.lower() for chunk in chunks]
    assert any("sharing" in title or "contacts" in title for title in titles)
    assert knowledge_retriever.has_documentation()


def test_retrieval_returns_nothing_for_an_unrelated_query():
    assert knowledge_retriever.retrieve("zzzz qqqq") == []


@pytest.mark.parametrize(
    "question",
    [
        "how to use this app",
        "how do I use this app",
        "how does this app work",
        "how do I get started",
    ],
)
def test_generic_app_questions_find_the_getting_started_section(question):
    titles = [chunk.title.lower() for chunk in knowledge_retriever.retrieve(question)]
    assert any("getting started" in title for title in titles)


def test_retrieval_prefers_the_section_named_after_the_topic():
    first = knowledge_retriever.retrieve("what is the trash")[0]
    assert first.title.strip().lower() == "trash"


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_TIMEOUT", "9")
    settings = llm_service.AISettings.from_env()
    assert settings.api_key == "abc"
    assert settings.model == "gemini-test"
    assert settings.enabled is False
    assert settings.timeout == 9.0


def test_service_reports_disabled_state(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    llm_service.reset_llm_service()
    try:
        assert llm_service.get_llm_service().status()["state"] == "disabled"
    finally:
        llm_service.reset_llm_service()


def test_voice_context_row_is_used_for_memory(login_as, app):
    vault, user = login_as()
    vault.voice("list my folders")
    with app.app_context():
        ctx = VoiceContext.query.filter_by(user_id=user.id).one()
        data = json.loads(ctx.context_json)
        assert data["turns"][0]["role"] == "user"
        assert "list my folders" in data["turns"][0]["text"]


# ---------------------------------------------------------------------------
# Latency: the provider asks for a short, non-reasoning answer
def _provider_with_fake_client(behaviour):
    provider = gemini_provider.GeminiProvider(api_key="test-key")

    class _Models:
        def __init__(self):
            self.configs = []

        def generate_content(self, *, model, contents, config):
            self.configs.append(config)
            outcome = behaviour(len(self.configs))
            if isinstance(outcome, Exception):
                raise outcome
            return type("Response", (), {"text": outcome})()

    models = _Models()
    provider._client = type("Client", (), {"models": models})()
    return provider, models


def test_provider_requests_a_capped_answer_without_deep_thinking():
    provider, models = _provider_with_fake_client(
        lambda _call: '{"category": "GENERAL_KNOWLEDGE", "answer": "hi"}'
    )
    result = provider.generate(system_instruction="s", user_prompt="p")

    assert result.ok is True
    config = models.configs[0]
    assert config.max_output_tokens == gemini_provider.DEFAULT_MAX_OUTPUT_TOKENS
    level = config.thinking_config.thinking_level
    assert getattr(level, "value", str(level)).lower() == gemini_provider.THINKING_LEVEL


def test_provider_retries_without_the_thinking_setting_if_the_model_rejects_it():
    def behaviour(call):
        if call == 1:
            return RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported")
        return '{"category": "GENERAL_KNOWLEDGE", "answer": "hi"}'

    provider, models = _provider_with_fake_client(behaviour)
    result = provider.generate(system_instruction="s", user_prompt="p")

    assert result.ok is True
    assert len(models.configs) == 2
    assert provider._thinking is False
    assert getattr(models.configs[1], "thinking_config", None) is None
