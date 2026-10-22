"""Step 1 and 2: classify the question, then gate it (docs/KNOWLEDGE-PLAN.md section 1).

The LLM decides the topic (it understands "glass of wine" is diet); the
deterministic keyword rules (``patient_questions.classify`` and
``intent.is_red_flag``) can only VETO. If either side says red flag, medication or
symptom, the question defers. The rules can make a question stricter, never looser.

When the keyword rules veto, the model is not called at all: the answer would not
change, and a live call should not wait for it.

``vetoed_by`` names the side whose verdict deferred the question at the safety
gate (red flag or a locked category): "keywords" or "llm". It is None for every
other outcome.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field
from voa_core import knowledge as kb
from voa_core import patient_questions as pq
from voa_core.intent import is_red_flag

log = logging.getLogger(__name__)

CLASSIFY_PROMPT_VERSION = "classify-v1"
#: Below this the LLM's topic is not trusted: the question defers.
LOW_CONFIDENCE = 0.6

# Reasons this step can defer with (stable strings, also in agent.REASONS).
R_EMPTY = "empty_question"
R_RED_FLAG = "red_flag"
R_LOCKED = "locked_category"
R_OTHER = "unclassified"
R_NOT_ALLOWED = "category_not_allowed"
R_LOW = "low_confidence"
#: The question is not in English: no language packs yet, so a nurse answers it.
R_LANGUAGE = "language"

VETO_KEYWORDS, VETO_LLM = "keywords", "llm"

CategoryName = Literal[
    "medication",
    "symptom",
    "appointment",
    "activity",
    "diet",
    "paperwork_billing",
    "supplies",
    "recovery",
    "other",
]


def load_prompt(name: str) -> str:
    return resources.files(__package__).joinpath("prompts", name).read_text()


class ClassifyOut(BaseModel):
    """The classifier model's structured output."""

    category: CategoryName = "other"
    red_flag: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass(frozen=True)
class LlmClassification:
    category: str
    red_flag: bool
    confidence: float


#: ``await classifier(question) -> LlmClassification``. Raises on any API error.
Classifier = Callable[[str], Awaitable[LlmClassification]]


@dataclass(frozen=True)
class Classification:
    """Both sides' verdicts and the one that counts (``category``)."""

    category: str
    keyword_category: str
    llm_category: str | None = None
    red_flag: bool = False
    vetoed_by: str | None = None
    confidence: float | None = None

    @property
    def category_label(self) -> str:
        return pq.CATEGORY_LABELS.get(self.category, "Other")

    @property
    def priority(self) -> str:
        """For a deferred question: the more urgent of the two classifications;
        an unclear question is immediate."""
        p = pq.more_urgent(pq.priority_of(self.keyword_category), pq.priority_of(self.category))
        if self.llm_category is not None:
            p = pq.more_urgent(p, pq.priority_of(self.llm_category))
        return pq.PRIORITY_IMMEDIATE if self.red_flag else p

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "category_label": self.category_label,
            "llm_category": self.llm_category,
            "keyword_category": self.keyword_category,
            "red_flag": self.red_flag,
            "vetoed_by": self.vetoed_by,
        }


def keyword_gate(question: str) -> tuple[Classification, str | None]:
    """The deterministic rules alone: (classification, reason to defer or None)."""
    kw = pq.classify(question).category
    if is_red_flag(question):
        return Classification(kw, kw, red_flag=True, vetoed_by=VETO_KEYWORDS), R_RED_FLAG
    if kw in kb.LOCKED_CATEGORIES:
        return Classification(kw, kw, vetoed_by=VETO_KEYWORDS), R_LOCKED
    from voa_core.parsers import not_english

    if not_english(question):
        # These English rules cannot read it (an English stem inside a Spanish
        # question misroutes it), and the clinic's answers are English only.
        return Classification(kw, kw, vetoed_by=VETO_KEYWORDS), R_LANGUAGE
    return Classification(kw, kw), None


def llm_gate(
    keyword: Classification, llm: LlmClassification, settings: Mapping[str, Any]
) -> tuple[Classification, str | None]:
    """Combine the LLM's verdict with the keyword one (already passed its veto)."""
    cat = llm.category if llm.category in pq.CATEGORY_LABELS else pq.CAT_OTHER
    try:
        conf = min(max(float(llm.confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = 0.0
    base = {"keyword_category": keyword.keyword_category, "llm_category": cat, "confidence": conf}
    if llm.red_flag is True:
        return Classification(cat, red_flag=True, vetoed_by=VETO_LLM, **base), R_RED_FLAG
    if cat in kb.LOCKED_CATEGORIES:
        return Classification(cat, vetoed_by=VETO_LLM, **base), R_LOCKED
    c = Classification(cat, **base)
    if conf < LOW_CONFIDENCE:
        return c, R_LOW
    if cat not in kb.ALLOWABLE_CATEGORIES:
        return c, R_OTHER
    if cat not in kb.normalize_settings(settings)["allowed_categories"]:
        return c, R_NOT_ALLOWED
    return c, None


def build_prompt(question: str) -> str:
    return f"Patient's question (between the markers):\n<<<\n{question[:300]}\n>>>"


class GeminiClassifier:
    """``await classifier(question) -> LlmClassification``: one structured-output
    call to ``cfg.models.knowledge_classify`` on Vertex. Sees only the question."""

    def __init__(self, cfg=None, *, model: str | None = None, client=None, **client_kwargs):
        self._model = model or cfg.models.knowledge_classify
        self._client = client
        if client_kwargs or cfg is None:
            self._kwargs = client_kwargs
        else:
            self._kwargs = {
                "enterprise": True,  # Vertex (Agent Platform), not the AI Studio API
                "project": cfg.gcp.project_id,
                "location": cfg.models.knowledge_location,
            }
        self._instruction = load_prompt("classify.md")
        self._warmed = False

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(**self._kwargs)
        return self._client

    async def warm(self) -> None:
        """Sign in and open the connection ahead of the first question (a cold
        client's first call has overrun the budget on its own)."""
        if self._warmed:
            return
        self._warmed = True
        try:
            await self._get_client().aio.models.get(model=self._model)
        except Exception:  # noqa: BLE001 - best effort
            self._warmed = False

    async def __call__(self, question: str) -> LlmClassification:
        from google.genai import types

        resp = await self._get_client().aio.models.generate_content(
            model=self._model,
            contents=build_prompt(question),
            config=types.GenerateContentConfig(
                system_instruction=self._instruction,
                temperature=0,
                response_mime_type="application/json",
                response_schema=ClassifyOut,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        out = (
            parsed
            if isinstance(parsed, ClassifyOut)
            else ClassifyOut.model_validate_json(resp.text or "{}")
        )
        return LlmClassification(out.category, out.red_flag, out.confidence)
