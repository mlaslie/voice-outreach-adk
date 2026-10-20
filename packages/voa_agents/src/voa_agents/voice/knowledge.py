"""The voice agent's knowledge-base tools (docs/KNOWLEDGE-PLAN.md, step 1).

Present only when an admin has switched the knowledge base on for voice
(``settings/knowledge``: ``enabled`` and ``channels.voice``). The gateway decides
that once at session start (:func:`prepare`), puts a small flag in session state
(``state["knowledge"]``) and registers the approved items and the matcher for the
session here. With the flag absent, :class:`KnowledgeToolset` exposes no tools and
the instruction is unchanged: the agent behaves exactly as before.

* ``answer_from_knowledge(question)`` records the question (like
  ``record_patient_question``) with its outcome, and returns the clinic's exact
  answer or "deferred".
* ``record_answer_feedback(helped)`` records the patient's reply to "Did that
  answer your question?". "no" turns the question into a nurse follow-up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.base_toolset import BaseToolset
from google.genai import types
from voa_core import knowledge
from voa_core import patient_questions as pq

log = logging.getLogger(__name__)

STATE_KEY = "knowledge"
SOURCE = "the clinic's answer"


@dataclass
class KnowledgeRuntime:
    items: list[dict[str, Any]]
    settings: dict[str, Any]
    matcher: knowledge.Matcher | None


_REGISTRY: dict[str, KnowledgeRuntime] = {}
_MATCHERS: dict[int, knowledge.Matcher] = {}


def register(session_id: str, runtime: KnowledgeRuntime) -> None:
    _REGISTRY[session_id] = runtime


def unregister(session_id: str) -> None:
    _REGISTRY.pop(session_id, None)


def runtime_for(session_id: str) -> KnowledgeRuntime | None:
    return _REGISTRY.get(session_id)


def _default_matcher(cfg) -> knowledge.Matcher:
    m = _MATCHERS.get(id(cfg))
    if m is None:
        from voa_agents.knowledge_matcher import GeminiMatcher

        m = _MATCHERS[id(cfg)] = GeminiMatcher(cfg)
    return m


async def prepare(store, cfg, *, matcher: knowledge.Matcher | None = None):
    """At session start: ``(state flag, runtime)`` when the knowledge base is on for
    voice, else ``(None, None)``. Never raises: a call is never failed over this."""
    try:
        settings = await knowledge.load_settings(store)
        if not knowledge.applies(settings, "voice"):
            return None, None
        items = await knowledge.load_approved(store)
        runtime = KnowledgeRuntime(items, settings, matcher or _default_matcher(cfg))
        return {"ask_satisfied": bool(settings["ask_satisfied"])}, runtime
    except Exception:
        log.exception("knowledge setup failed", extra={"event": "knowledge.setup_failed"})
        return None, None


def _session_id(tool_context: ToolContext) -> str:
    return getattr(getattr(tool_context, "session", None), "id", "")


def _audit(tool_context: ToolContext, name: str, **fields: Any) -> None:
    from .tools import _audit as audit

    audit(tool_context, name, **fields)


async def answer_from_knowledge(question: str, tool_context: ToolContext) -> dict:
    """Look up the caller's question in the clinic's approved answers, and record it.

    Args:
        question: A short summary of the caller's question, in their words,
            e.g. "when can I shower".

    Returns:
        outcome "answered" with the clinic's answer to say word for word, or
        "deferred" (the care team will follow up). The question is recorded either way.
    """
    from voa_core import survey

    if not (question or "").strip():
        return {"status": "error", "message": "Pass the caller's question."}
    answers = dict(tool_context.state.get("answers", {}))
    qs = list(answers.get(survey.F_PATIENT_QUESTIONS, []))
    if len(qs) >= pq.MAX_QUESTIONS:
        _audit(tool_context, "answer_from_knowledge", status="limit")
        return {
            "outcome": pq.OUTCOME_DEFERRED,
            "status": "limit",
            "say": "Tell them the care team will go over the rest of their questions, "
            "then move on.",
        }
    entry: dict[str, Any] = pq.record(question)
    rt = runtime_for(_session_id(tool_context))
    if rt is None:
        result = knowledge.KnowledgeResult(pq.OUTCOME_DEFERRED, entry["category"], "not_set_up")
    else:
        result = await knowledge.answer(
            entry["text"], items=rt.items, settings=rt.settings, matcher=rt.matcher
        )
    entry["outcome"] = result.outcome
    if result.answered:
        entry.update(
            item_id=result.item_id,
            item_version=result.item_version,
            answer=result.answer,
            satisfied=None,
        )
    qs.append(entry)
    answers[survey.F_PATIENT_QUESTIONS] = qs
    answers.setdefault(survey.Q_HAS_QUESTIONS, "yes")
    tool_context.state["answers"] = answers
    # Field names, the triage and the outcome only: the question itself is PHI-adjacent.
    _audit(
        tool_context,
        "answer_from_knowledge",
        status="recorded",
        outcome=result.outcome,
        reason=result.reason,
        category=entry["category"],
        priority=entry["priority"],
        item_id=result.item_id,
        item_version=result.item_version,
        confidence=result.confidence,
        count=len(qs),
    )
    if result.answered:
        return {"outcome": pq.OUTCOME_ANSWERED, "answer": result.answer, "source": SOURCE}
    out: dict[str, Any] = {"outcome": pq.OUTCOME_DEFERRED}
    if result.reason == knowledge.R_RED_FLAG:
        out["safety"] = "This may be an emergency sign: follow the Safety rule now."
    return out


async def record_answer_feedback(helped: str, tool_context: ToolContext) -> dict:
    """Record the caller's reply to "Did that answer your question?".

    Args:
        helped: "yes" if the clinic's answer answered their question, "no" if it did not.

    Returns:
        status "recorded"; for "no", say the care team will follow up on it.
    """
    from voa_core import survey

    said = str(helped or "").strip().lower()
    if said in {"yes", "y", "yeah", "yep", "true"}:
        satisfied = True
    elif said in {"no", "n", "nope", "false", "not really"}:
        satisfied = False
    else:
        return {"status": "error", "message": 'Pass "yes" or "no".'}
    answers = dict(tool_context.state.get("answers", {}))
    qs = [dict(q) for q in answers.get(survey.F_PATIENT_QUESTIONS, [])]
    idx = next(
        (
            i
            for i in range(len(qs) - 1, -1, -1)
            if qs[i].get("outcome") == pq.OUTCOME_ANSWERED and qs[i].get("satisfied") is None
        ),
        None,
    )
    if idx is None:
        return {"status": "error", "message": "No answered question is waiting for a reply."}
    qs[idx]["satisfied"] = satisfied
    if not satisfied:
        qs[idx]["outcome"] = pq.OUTCOME_DEFERRED  # triaged as if it had been deferred
    answers[survey.F_PATIENT_QUESTIONS] = qs
    tool_context.state["answers"] = answers
    _audit(tool_context, "record_answer_feedback", satisfied=satisfied)
    if satisfied:
        return {"status": "recorded"}
    return {"status": "recorded", "say": "Tell them the care team will follow up on it."}


def build_knowledge_tools() -> list[FunctionTool]:
    tools = []
    for fn in (answer_from_knowledge, record_answer_feedback):
        tool = FunctionTool(fn)
        tool.behavior = types.Behavior.BLOCKING
        tools.append(tool)
    return tools


class KnowledgeToolset(BaseToolset):
    """The knowledge tools, only for a session the gateway switched them on for."""

    def __init__(self) -> None:
        super().__init__()
        self._tools = build_knowledge_tools()

    async def get_tools(self, readonly_context=None) -> list:
        state = getattr(readonly_context, "state", None) or {}
        return list(self._tools) if state.get(STATE_KEY) else []
