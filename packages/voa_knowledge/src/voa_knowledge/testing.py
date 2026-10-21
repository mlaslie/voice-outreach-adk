"""Offline fakes for the knowledge agent's model and HTTP calls.

Used by the unit tests, the offline evaluation in CI and local dev: nothing here
touches the network. Each fake records its calls in ``.calls``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from voa_core import patient_questions as pq

from .agent import KnowledgeAgent
from .assemble import KIND_EXAMPLE, Material
from .classify import LlmClassification
from .draft import Draft
from .ground import Fact, GroundingResult


class FixedClassifier:
    """Always the same verdict."""

    def __init__(self, category: str = "activity", *, red_flag=False, confidence=1.0):
        self.result = LlmClassification(category, red_flag, confidence)
        self.calls: list[str] = []

    async def __call__(self, question: str) -> LlmClassification:
        self.calls.append(question)
        return self.result


class KeywordClassifier:
    """The deterministic rules as the "LLM": the keyword category, fully sure."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, question: str) -> LlmClassification:
        self.calls.append(question)
        return LlmClassification(pq.classify(question).category, False, 1.0)


class LabelClassifier:
    """Returns a label from a lookup (the evaluation set's own labels)."""

    def __init__(self, labels: Mapping[str, tuple[str, bool]]):
        self.labels = dict(labels)
        self.calls: list[str] = []

    async def __call__(self, question: str) -> LlmClassification:
        self.calls.append(question)
        category, red = self.labels[question]
        return LlmClassification(category, red, 1.0)


_STOP = frozenset(
    """a an the i my me we our you your to of for on in at is it its be am are was do does
    did can could should would will when where what which who how how's what's whats there
    any some more again yet ok okay after before with and or if this that get go allowed
    much soon long until about have has need question clinic's answer also asked as""".split()
)


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z']+", (text or "").lower()):
        w = w.strip("'").removesuffix("'s")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in _STOP:
            out.add(w)
    return out


class OverlapDrafter:
    """A deterministic stand-in for the drafting model: the example whose wording
    shares the most content words with the question (at least ``threshold`` of the
    wording's words) is "covered", and its answer is returned word for word. A
    guideline is used only when its title words are all in the question."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.calls: list[tuple[str, dict, list[str]]] = []

    async def __call__(
        self, question: str, context: Mapping[str, Any], materials: Sequence[Material]
    ) -> Draft:
        self.calls.append((question, dict(context), [m.ref for m in materials]))
        asked = words(question)
        best: tuple[float, Material | None] = (0.0, None)
        for m in materials:
            if m.kind == KIND_EXAMPLE:
                q = m.text.split("\nClinic's answer:", 1)[0].removeprefix("Question: ")
                wordings = re.split(r" \(also asked as: |; ", q.rstrip(")"))
            else:
                wordings = [m.title]
            for w in wordings:
                want = words(w)
                if want:
                    score = len(want & asked) / len(want)
                    if score > best[0]:
                        best = (score, m)
        score, m = best
        if m is None or score < (self.threshold if m.kind == KIND_EXAMPLE else 1.0):
            return Draft(False)
        if m.kind == KIND_EXAMPLE:
            answer = m.text.split("\nClinic's answer:", 1)[1].strip()
        else:
            answer = m.text.split("\n", 1)[-1].split(". ")[0].strip().rstrip(".") + "."
        return Draft(True, answer, (m.ref,))


class RecklessDrafter:
    """The worst case: always "covered", citing the first material, saying
    ``answer`` (default: the first material's text)."""

    def __init__(self, answer: str | None = None):
        self.answer = answer
        self.calls: list[str] = []

    async def __call__(self, question, context, materials) -> Draft:
        self.calls.append(question)
        if not materials:
            return Draft(True, self.answer or "Yes.", ("ex:nothing",))
        m = materials[0]
        return Draft(True, self.answer or m.text[:300], (m.ref,))


class FixedGrounder:
    def __init__(self, score: float = 1.0, *, uncited: int = 0):
        self.result = GroundingResult(score, 1, uncited)
        self.calls: list[tuple[str, list[Fact]]] = []

    async def __call__(self, answer: str, facts: Sequence[Fact]) -> GroundingResult:
        self.calls.append((answer, list(facts)))
        return self.result


def offline_agent(**overrides: Any) -> KnowledgeAgent:
    """A fully offline agent: keyword classifier, overlap drafter, full support."""
    kw: dict[str, Any] = {
        "classifier": KeywordClassifier(),
        "drafter": OverlapDrafter(),
        "grounder": FixedGrounder(1.0),
    }
    kw.update(overrides)
    return KnowledgeAgent(**kw)
