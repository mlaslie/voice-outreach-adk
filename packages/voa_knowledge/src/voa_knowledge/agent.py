"""The knowledge agent: question in, ``answered`` + text or ``deferred`` out.

``KnowledgeAgent.answer(question, context, settings=..., snapshot=... | store=...)``
runs the five steps of docs/KNOWLEDGE-PLAN.md. Every failure defers:

1. **classify**: keyword rules first (a red flag or a medication/symptom wording
   defers at once, no model call), then the LLM classifier.
2. **gate**: red flag, locked category, low confidence, unclassified, a category
   the clinic has not allowed: defer.
3. **assemble**: approved examples + the approved guidelines that apply to this
   patient (the ``Retriever``). None: defer without a model call.
4. **draft**: the ADK ``knowledge_agent``; ``covered`` false or no valid source:
   defer.
5. **verify**: check-grounding on the cited sources (``support_score`` must reach
   ``min_support``; an uncited claim fails), then the output veto (no medication,
   dose or symptom wording, no red flag, at most ``MAX_ANSWER_CHARS``).

Steps 1 (the LLM part) to 5 share one time budget (4 s by default); running out
defers with ``timeout``. Every model and HTTP call is injected, so tests use fakes.
The patient context is procedure, days since surgery, care setting and diabetic,
never identifiers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_core.intent import is_red_flag

from . import classify as cl
from .assemble import AllApprovedRetriever, Material, Retriever
from .draft import Drafter
from .ground import Fact, GroundingChecker

log = logging.getLogger(__name__)

DEFAULT_BUDGET_S = 4.0
MAX_ANSWER_CHARS = pq.MAX_ANSWER_CHARS

OUTCOME_ANSWERED, OUTCOME_DEFERRED = pq.OUTCOME_ANSWERED, pq.OUTCOME_DEFERRED

R_ANSWERED = "answered"
R_EMPTY, R_RED_FLAG, R_LOCKED = cl.R_EMPTY, cl.R_RED_FLAG, cl.R_LOCKED
R_OTHER, R_NOT_ALLOWED, R_LOW = cl.R_OTHER, cl.R_NOT_ALLOWED, cl.R_LOW
R_NO_KNOWLEDGE = "no_knowledge"
R_NOT_COVERED = "not_covered"
R_LOW_SUPPORT = "low_support"
R_OUTPUT_VETO = "output_veto"
R_TIMEOUT = "timeout"
R_ERROR = "error"
#: Every reason (docs/KNOWLEDGE-PLAN.md section 5), stable strings.
REASONS = (
    R_ANSWERED,
    R_EMPTY,
    R_RED_FLAG,
    R_LOCKED,
    R_OTHER,
    R_NOT_ALLOWED,
    R_LOW,
    R_NO_KNOWLEDGE,
    R_NOT_COVERED,
    R_LOW_SUPPORT,
    R_OUTPUT_VETO,
    R_TIMEOUT,
    R_ERROR,
)

_DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|milligrams?|micrograms?|ml|milliliters?|cc|units?|iu)\b"
    r"|\b(?:one|two|three|four|five|six|\d+)\s+(?:times|doses?)\s+(?:a|per)\s+day\b",
    re.I,
)


def output_veto(answer: str) -> bool:
    """True when the drafted answer must not be said: medication, dose or symptom
    wording (the same keyword rules as the question gate), or a red flag."""
    a = answer or ""
    return (
        pq.classify(a).category in kb.LOCKED_CATEGORIES
        or is_red_flag(a)
        or bool(_DOSE.search(a))
        or len(a) > MAX_ANSWER_CHARS
    )


@dataclass(frozen=True)
class KnowledgeResult:
    outcome: str  # answered | deferred
    reason: str
    classification: cl.Classification
    answer: str | None = None
    sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    support_score: float | None = None
    latency_ms: int = 0

    @property
    def answered(self) -> bool:
        return self.outcome == OUTCOME_ANSWERED

    @property
    def category(self) -> str:
        return self.classification.category

    @property
    def priority(self) -> str:
        return self.classification.priority

    def to_dict(self) -> dict[str, Any]:
        """The "Try it" shape (without ``note``)."""
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "classification": self.classification.to_dict(),
            "answer": self.answer,
            "sources": [dict(s) for s in self.sources],
            "support_score": self.support_score,
            "latency_ms": self.latency_ms,
        }


class KnowledgeAgent:
    def __init__(
        self,
        *,
        classifier: cl.Classifier,
        drafter: Drafter,
        grounder: GroundingChecker,
        retriever: Retriever | None = None,
        budget_s: float = DEFAULT_BUDGET_S,
    ):
        self.classifier = classifier
        self.drafter = drafter
        self.grounder = grounder
        self.retriever = retriever or AllApprovedRetriever()
        self.budget_s = budget_s

    @classmethod
    def from_config(cls, cfg, *, budget_s: float = DEFAULT_BUDGET_S) -> KnowledgeAgent:
        """Production wiring: Gemini classifier, the ADK drafter, Vertex grounding."""
        from .draft import AdkDrafter
        from .ground import VertexGroundingChecker

        return cls(
            classifier=cl.GeminiClassifier(cfg),
            drafter=AdkDrafter(cfg),
            grounder=VertexGroundingChecker(cfg.gcp.project_id),
            budget_s=budget_s,
        )

    async def warm(self) -> None:
        """Sign in to every backend ahead of the first question (best effort)."""
        warms = [
            w()
            for part in (self.classifier, self.drafter, self.grounder)
            if (w := getattr(part, "warm", None)) is not None
        ]
        await asyncio.gather(*warms, return_exceptions=True)

    async def answer(
        self,
        question: str,
        context: Mapping[str, Any] | None = None,
        *,
        settings: Mapping[str, Any],
        snapshot: kb.KnowledgeSnapshot | None = None,
        store=None,
    ) -> KnowledgeResult:
        """Answer ``question`` or defer. ``settings`` supplies the allowed
        categories and ``min_support``; whether the agent is on for a channel is
        the caller's decision, so "Try it" runs it in any mode."""
        t0 = time.monotonic()
        q = " ".join((question or "").split())[: pq.MAX_QUESTION_CHARS]
        ctx = dict(context or {})
        s = kb.normalize_settings(settings)

        def done(reason: str, c: cl.Classification, **kw: Any) -> KnowledgeResult:
            outcome = OUTCOME_ANSWERED if reason == R_ANSWERED else OUTCOME_DEFERRED
            r = KnowledgeResult(
                outcome, reason, c, latency_ms=round((time.monotonic() - t0) * 1000), **kw
            )
            log.info(
                "knowledge answer",
                extra={
                    "event": "knowledge.result",
                    "outcome": outcome,
                    "reason": reason,
                    "category": c.category,
                    "vetoed_by": c.vetoed_by,
                    "support_score": r.support_score,
                    "latency_ms": r.latency_ms,
                },
            )
            return r

        keyword, reason = cl.keyword_gate(q)
        if not q:
            return done(R_EMPTY, keyword)
        if reason:
            return done(reason, keyword)

        state: dict[str, Any] = {"c": keyword}
        try:
            async with asyncio.timeout(self.budget_s):
                return await self._rest(q, ctx, s, keyword, snapshot, store, state, done)
        except TimeoutError:
            log.warning("knowledge timed out", extra={"event": "knowledge.timeout"})
            return done(R_TIMEOUT, state["c"])
        except Exception:
            log.exception("knowledge failed", extra={"event": "knowledge.error"})
            return done(R_ERROR, state["c"])

    async def _rest(self, q, ctx, s, keyword, snapshot, store, state, done) -> KnowledgeResult:
        # 1-2: the LLM's classification and the gate
        c, reason = cl.llm_gate(keyword, await self.classifier(q), s)
        state["c"] = c
        if reason:
            return done(reason, c)

        # 3: assemble
        if snapshot is None:
            snapshot = (
                await kb.load_snapshot(store) if store is not None else kb.KnowledgeSnapshot()
            )
        materials: list[Material] = list(await self.retriever.retrieve(q, ctx, snapshot))
        if not materials:
            return done(R_NO_KNOWLEDGE, c)

        # 4: draft
        draft = await self.drafter(q, ctx, materials)
        by_ref = {m.ref: m for m in materials}
        cited = [by_ref[i] for i in dict.fromkeys(draft.source_ids) if i in by_ref]
        answer = (draft.answer or "").strip()
        if not draft.covered or not cited or not answer:
            return done(R_NOT_COVERED, c)
        sources = tuple(m.source() for m in cited)

        # 5: verify (grounding, then the output veto)
        facts = [Fact(text, m.ref) for m in cited for text in m.facts()]
        g = await self.grounder(answer, facts)
        score = round(min(max(float(g.support_score), 0.0), 1.0), 4)
        if score < s["min_support"] or g.uncited_claims:
            return done(R_LOW_SUPPORT, c, sources=sources, support_score=score)
        if output_veto(answer):
            return done(R_OUTPUT_VETO, c, sources=sources, support_score=score)
        return done(R_ANSWERED, c, answer=answer, sources=sources, support_score=score)
