"""The voice agent's knowledge tools (docs/KNOWLEDGE-PLAN.md v2, live mode).

Present only when an admin has set the knowledge agent to **live** for voice
(``settings/knowledge``: ``mode == "live"`` and ``channels.voice``). The gateway
decides that once at session start (:func:`prepare`), puts a small flag in session
state (``state["knowledge"]``) and registers the session's runtime here. Off and
shadow leave the flag absent: :class:`KnowledgeToolset` then exposes no tools and
the instruction is unchanged, so the agent behaves exactly as before.

* ``answer_from_knowledge(question)`` records the question (like
  ``record_patient_question``) and asks the knowledge agent
  (``voa_knowledge.KnowledgeAgent``) with the patient context from session state:
  procedure, days since surgery, care setting, diabetic. Never the name, DOB,
  phone or MRN. It returns the answer to read out, or "deferred".
* ``record_answer_feedback(helped)`` records the patient's reply to "Did that
  answer your question?". "no" turns the question into a nurse follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
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
    snapshot: knowledge.KnowledgeSnapshot
    settings: dict[str, Any]
    agent: Any  # voa_knowledge.KnowledgeAgent (or a test fake with .answer)

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.snapshot.examples


_REGISTRY: dict[str, KnowledgeRuntime] = {}
_AGENTS: dict[int, Any] = {}


def register(session_id: str, runtime: KnowledgeRuntime) -> None:
    _REGISTRY[session_id] = runtime


def unregister(session_id: str) -> None:
    _REGISTRY.pop(session_id, None)


def runtime_for(session_id: str) -> KnowledgeRuntime | None:
    return _REGISTRY.get(session_id)


def default_agent(cfg) -> Any:
    """One knowledge agent per process and config (its clients are reused)."""
    a = _AGENTS.get(id(cfg))
    if a is None:
        from voa_knowledge import KnowledgeAgent

        a = _AGENTS[id(cfg)] = KnowledgeAgent.from_config(cfg)
    return a


_WARMING: set = set()


def warm_up(agent: Any) -> None:
    """Start the agent's sign-in in the background (the greeting and identity
    check take far longer than it needs), keeping a reference to the task."""
    warm = getattr(agent, "warm", None)
    if warm is None:
        return
    task = asyncio.get_running_loop().create_task(warm())
    _WARMING.add(task)
    task.add_done_callback(_WARMING.discard)


async def prepare(store, cfg, *, agent: Any = None):
    """At session start: ``(state flag, runtime)`` when the knowledge agent is live
    for voice, else ``(None, None)`` (off and shadow). Never raises: a call is
    never failed over this."""
    try:
        settings = await knowledge.load_settings(store)
        if not knowledge.applies(settings, "voice"):
            return None, None
        snapshot = await knowledge.load_snapshot(store)
        a = agent or default_agent(cfg)
        warm_up(a)
        runtime = KnowledgeRuntime(snapshot, settings, a)
        return {"ask_satisfied": bool(settings["ask_satisfied"])}, runtime
    except Exception:
        log.exception("knowledge setup failed", extra={"event": "knowledge.setup_failed"})
        return None, None


def _session_id(tool_context: ToolContext) -> str:
    return getattr(getattr(tool_context, "session", None), "id", "")


def _audit(tool_context: ToolContext, name: str, **fields: Any) -> None:
    from .tools import _audit as audit

    audit(tool_context, name, **fields)


def call_context(patient: dict[str, Any] | None) -> dict[str, Any]:
    """The knowledge agent's patient context, from ``state["patient"]``."""
    return knowledge.patient_context(patient, today=datetime.now(UTC).date())


async def answer_from_knowledge(question: str, tool_context: ToolContext) -> dict:
    """Look up the caller's question in the clinic's approved material, and record it.

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
    result = None
    if rt is not None:
        try:
            result = await rt.agent.answer(
                entry["text"],
                call_context(tool_context.state.get("patient")),
                settings=rt.settings,
                snapshot=rt.snapshot,
            )
        except Exception:  # the agent defers on its own; this is a last resort
            log.exception("knowledge agent failed", extra={"event": "knowledge.tool_error"})
    if result is None:
        entry["outcome"] = pq.OUTCOME_DEFERRED
        reason = "not_set_up" if rt is None else "error"
    else:
        reason = result.reason
        entry["outcome"] = result.outcome
        # The more urgent of the keyword and LLM classifications (an unclear
        # question is immediate); the category is the one that counted.
        entry["category"] = result.category
        entry["priority"] = pq.more_urgent(entry["priority"], result.priority)
        if result.answered:
            entry.update(
                answer=result.answer,
                sources=[dict(s) for s in result.sources],
                support_score=result.support_score,
                satisfied=None,
            )
    qs.append(entry)
    answers[survey.F_PATIENT_QUESTIONS] = qs
    from .tools import _campaign

    for qid in _campaign(tool_context).open_question_ids:  # post-op: has_questions
        answers.setdefault(qid, "yes")
    tool_context.state["answers"] = answers
    # Field names, the triage and the outcome only: the question itself is PHI-adjacent.
    _audit(
        tool_context,
        "answer_from_knowledge",
        status="recorded",
        outcome=entry["outcome"],
        reason=reason,
        category=entry["category"],
        priority=entry["priority"],
        sources=len(entry.get("sources") or []),
        support_score=entry.get("support_score"),
        latency_ms=getattr(result, "latency_ms", None),
        count=len(qs),
    )
    if entry["outcome"] == pq.OUTCOME_ANSWERED:
        return {"outcome": pq.OUTCOME_ANSWERED, "answer": entry["answer"], "source": SOURCE}
    out: dict[str, Any] = {"outcome": pq.OUTCOME_DEFERRED}
    if reason == "red_flag":
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
