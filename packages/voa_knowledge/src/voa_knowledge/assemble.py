"""Step 3: the material the draft may use (docs/KNOWLEDGE-PLAN.md section 2).

No RAG at clinic scale: every approved example, plus every approved guideline that
applies to this patient's procedure and care setting, goes into the prompt. The
:class:`Retriever` seam lets Vertex AI RAG Engine replace "send everything" when a
clinic outgrows the budget, without touching the rest of the agent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from voa_core import knowledge as kb

KIND_EXAMPLE, KIND_GUIDELINE = "example", "guideline"
#: The grounding API takes at most 10,000 characters per fact.
MAX_FACT_CHARS = 10_000


@dataclass(frozen=True)
class Material:
    """One piece of approved content, as the draft and the grounding check see it."""

    kind: str  # example | guideline
    id: str  # the item_id or doc_id
    title: str
    version: int
    text: str  # what the drafting model reads

    @property
    def ref(self) -> str:
        """The id the drafting model cites (unique across kinds)."""
        return f"{'ex' if self.kind == KIND_EXAMPLE else 'gl'}:{self.id}"

    def facts(self) -> list[str]:
        """Grounding facts: the text, split on paragraphs to fit the API's limit."""
        return split_text(self.text, MAX_FACT_CHARS)

    def source(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "title": self.title, "version": self.version}


def split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if len(cur) + len(para) + 1 > limit:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()]


def example_material(item: Mapping[str, Any]) -> Material | None:
    if item.get("status") != kb.APPROVED or not item.get("item_id"):
        return None
    answer = str(item.get("answer") or "").strip()
    question = str(item.get("question") or "").strip()
    if not answer or not question:
        return None
    alts = [str(a) for a in item.get("alt_questions") or [] if str(a).strip()]
    asked = question + (f" (also asked as: {'; '.join(alts)})" if alts else "")
    return Material(
        kind=KIND_EXAMPLE,
        id=str(item["item_id"]),
        title=question,
        version=int(item.get("version") or 1),
        text=f"Question: {asked}\nClinic's answer: {answer}",
    )


def guideline_material(doc: Mapping[str, Any]) -> Material | None:
    if doc.get("status") != kb.APPROVED or not doc.get("doc_id"):
        return None
    body = str(doc.get("body") or "").strip()
    if not body:
        return None
    title = str(doc.get("title") or "").strip()
    return Material(
        kind=KIND_GUIDELINE,
        id=str(doc["doc_id"]),
        title=title,
        version=int(doc.get("version") or 1),
        text=f"{title}\n{body}" if title else body,
    )


class Retriever(Protocol):
    async def retrieve(
        self,
        question: str,
        context: Mapping[str, Any],
        snapshot: kb.KnowledgeSnapshot,
    ) -> Sequence[Material]: ...


class AllApprovedRetriever:
    """Every approved example, plus the approved guidelines that apply to this
    patient (``voa_core.knowledge.document_applies``)."""

    async def retrieve(
        self,
        question: str,
        context: Mapping[str, Any],
        snapshot: kb.KnowledgeSnapshot,
    ) -> list[Material]:
        out: list[Material] = []
        for it in snapshot.examples:
            if m := example_material(it):
                out.append(m)
        for doc in snapshot.guidelines:
            if not kb.document_applies(
                doc,
                procedure=context.get("procedure"),
                care_setting=context.get("care_setting"),
            ):
                continue
            if m := guideline_material(doc):
                out.append(m)
        return out
