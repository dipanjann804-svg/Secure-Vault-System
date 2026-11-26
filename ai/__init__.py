"""Gemini-powered conversational layer for the Secure Vault voice assistant.

The package is deliberately framework-free: nothing here imports Flask, reads
``current_user``, or touches the database. It receives text plus the metadata
the route layer has already authorised, and returns a *proposal*. The Flask
routes remain the only place that can execute a vault operation.

Modules
-------
``schemas``            Vocabulary, tool catalogue and strict validation.
``provider_base``      The provider interface a second LLM can implement.
``gemini_provider``    Google Gemini implementation (official google-genai SDK).
``response_parser``    JSON extraction; malformed output is rejected, never guessed.
``prompt_manager``     System instruction and prompt assembly.
``knowledge_retriever`` Local, verified RAG corpus and retrieval.
``conversation_manager`` Short-lived, per-user conversational references.
``request_router``     Turns a validated proposal into a deterministic plan.
``llm_service``        Wiring, configuration and the ``decide`` entry point.
"""

__all__ = [
    "schemas",
    "provider_base",
    "gemini_provider",
    "response_parser",
    "prompt_manager",
    "knowledge_retriever",
    "conversation_manager",
    "request_router",
    "llm_service",
]
