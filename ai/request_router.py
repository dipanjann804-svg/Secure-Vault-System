"""Turn a validated model proposal into a plan the existing handlers can run.

This is the boundary between "the model said something" and "the application
will do something". A proposal only becomes a plan when:

* its category is one this module handles,
* its tool name is in ``ai.schemas.TOOLS`` (validated before it gets here),
* and its parameters already passed strict validation.

The resulting plan is an ordinary ``voice_service`` plan, so the same Flask
handlers, owner checks, rate limits and confirmation flow apply as for a
deterministically parsed command. Nothing here touches the database.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai import schemas
import voice_service

NO_LIVE_SOURCES = (
    "I can't check live information like weather, news, prices, or scores. "
    "No search provider is configured for this assistant, so I won't guess. "
    "I can answer questions about your vault, or explain technical topics."
)

DEFAULT_REFUSAL = (
    "I can't do that by voice. Open the interface so you can review the "
    "confirmation, and I'll help with anything read-only."
)

NO_DOCUMENTED_ANSWER = (
    "I don't have that documented for Secure Vault, and I won't invent an "
    "answer. I can explain what the app does with files, notes, contacts, and "
    "share links."
)


@dataclass(frozen=True)
class RouteResult:
    """A plan plus the metadata the response envelope reports."""

    plan: dict
    category: str
    tool_name: str | None
    requires_confirmation: bool
    source: str = "ai"


def _answer_route(decision: schemas.Decision) -> RouteResult:
    speech = decision.speech or decision.answer
    display = decision.answer or decision.speech
    plan = voice_service.answer_plan(speech, display)
    return RouteResult(plan, decision.category, None, False)


def route(decision: schemas.Decision, text: str = "") -> RouteResult | None:
    """Route one validated decision, or return None to use the fallback."""
    if decision is None or decision.error_code:
        return None

    category = decision.category

    if category == schemas.CATEGORY_VAULT_TOOL or category == schemas.CATEGORY_FOLLOW_UP:
        spec = decision.tool
        if spec is None:
            return None
        plan = voice_service.plan_from_tool_call(
            intent=spec.intent,
            tier=spec.tier,
            audit_tool=spec.audit,
            speech=decision.speech or spec.speech,
            params=decision.plan_params,
            action=spec.action,
        )
        return RouteResult(plan, category, spec.name, spec.is_mutation)

    if category == schemas.CATEGORY_CLARIFICATION:
        return RouteResult(
            voice_service.clarify_plan(decision.clarification),
            category,
            None,
            False,
        )

    if category == schemas.CATEGORY_REFUSED:
        message = decision.refusal_reason or decision.speech or DEFAULT_REFUSAL
        return RouteResult(
            voice_service.blocked_plan(message, "forbidden_action"),
            category,
            None,
            False,
        )

    if category == schemas.CATEGORY_CURRENT:
        # Even if the model produced a "current" answer, it is not grounded.
        plan = voice_service.limit_plan(NO_LIVE_SOURCES)
        return RouteResult(plan, category, None, False)

    if category == schemas.CATEGORY_VAULT_HELP:
        if not (decision.answer or decision.speech):
            plan = voice_service.limit_plan(NO_DOCUMENTED_ANSWER)
            return RouteResult(plan, category, None, False)
        return _answer_route(decision)

    if category in (schemas.CATEGORY_GENERAL, schemas.CATEGORY_TECHNICAL):
        return _answer_route(decision)

    return None
