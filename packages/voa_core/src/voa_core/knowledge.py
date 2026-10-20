"""Answers from the clinic's approved Q&A (knowledge base step 1, docs/KNOWLEDGE-PLAN.md).

One shared function, :func:`answer`, decides whether a patient's question gets the
clinic's own written answer or is deferred to a nurse. It is channel-agnostic: the
voice tool calls it today, the text questionnaire can call it the same way later.

Order, safest first. Every step that fails defers:

1. **Red flag** (``intent.is_red_flag``): defer. The caller's emergency path runs.
2. **Category gate** (``patient_questions.classify``), deterministic:
   medication and symptom ALWAYS defer, whatever the settings say; "other" defers;
   a category the clinic has not allowed defers.
3. **Candidates**: the approved items in the question's category. None: defer
   without calling a model. Matching across categories was tried and widened
   false matches (the eval set caught one), so instead an item is refused when
   it is saved if its question reads as a different topic than its category
   (:func:`check_topic`): an item can never sit unused without anyone knowing.
4. **Matcher** (injected; Gemini in production, ``voa_agents.knowledge_matcher``):
   picks one candidate or none, with a confidence. Answered only if the id is one
   of the candidates AND the confidence reaches ``min_match``. Timeout or error:
   defer.

What the patient hears is the item's exact ``answer`` text. Nothing is generated.
The matcher sees only the candidates' questions and the patient's question: never
the answers, never the patient's name or identifiers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import patient_questions as pq

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- storage

ITEMS = "knowledge_items"
SETTINGS_COLLECTION = "settings"
SETTINGS_DOC = "knowledge"
CACHE_S = 60.0

DRAFT, APPROVED, RETIRED = "draft", "approved", "retired"
STATUSES = (DRAFT, APPROVED, RETIRED)

# --------------------------------------------------------------------------- categories

#: Never answered, in any configuration (also refused by the settings API).
LOCKED_CATEGORIES: tuple[str, ...] = (pq.CAT_MEDICATION, pq.CAT_SYMPTOM)
#: Never answerable either: an unclassified question goes to a nurse.
NEVER_ANSWERED: frozenset[str] = frozenset({*LOCKED_CATEGORIES, pq.CAT_OTHER})
#: Categories an admin may allow, and an item may belong to.
ALLOWABLE_CATEGORIES: tuple[str, ...] = tuple(
    c for c in pq.CATEGORY_LABELS if c not in NEVER_ANSWERED
)

# --------------------------------------------------------------------------- limits

MAX_QUESTION = 300
MAX_ANSWER = 600
MAX_ALT_QUESTIONS = 10
MAX_ITEMS = 500
DEFAULT_MIN_MATCH = 0.8
MIN_MATCH_RANGE = (0.5, 1.0)
MATCH_TIMEOUT_S = 3.0

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "channels": {"voice": True, "sms": True},
    "allowed_categories": list(ALLOWABLE_CATEGORIES),
    "min_match": DEFAULT_MIN_MATCH,
    "ask_satisfied": True,
}
CHANNELS = ("voice", "sms")

# Reasons (stable strings: they are shown in "Try it" and logged).
R_MATCHED = "matched"
R_EMPTY = "empty_question"
R_RED_FLAG = "red_flag"
R_LOCKED = "locked_category"
R_OTHER = "unclassified"
R_NOT_ALLOWED = "category_not_allowed"
R_NO_CANDIDATES = "no_approved_items"
R_NO_MATCH = "no_match"
R_LOW = "low_confidence"
R_TIMEOUT = "timeout"
R_ERROR = "error"

# --------------------------------------------------------------------------- PHI check

_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "a date of birth",
        re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b|\b\d{4}-\d{2}-\d{2}\b"),
    ),
    (
        "a phone number",
        re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    ),
    ("an ID number (6 or more digits)", re.compile(r"\d{6,}")),
)


def phi_problems(text: str) -> list[str]:
    """What obvious PHI ``text`` seems to hold (empty when none). Clinic material
    only: a phone number, a date like 03/15/1950, an email, an MRN-like digit run."""
    t = text or ""
    return [what for what, rx in _PHI_PATTERNS if rx.search(t)]


# --------------------------------------------------------------------------- validation


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def clean_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the editable fields that are present in ``fields``
    (question, answer, category, alt_questions). Raises ValueError with a message
    an admin can act on."""
    out: dict[str, Any] = {}
    if "question" in fields:
        q = _clean(fields["question"])
        if not q:
            raise ValueError("The question is required.")
        if len(q) > MAX_QUESTION:
            raise ValueError(f"The question must be at most {MAX_QUESTION} characters.")
        out["question"] = q
    if "answer" in fields:
        a = str(fields["answer"] or "").strip()
        a = "\n".join(" ".join(line.split()) for line in a.splitlines()).strip()
        if not a:
            raise ValueError("The answer is required.")
        if len(a) > MAX_ANSWER:
            raise ValueError(f"The answer must be at most {MAX_ANSWER} characters.")
        out["answer"] = a
    if "category" in fields:
        c = fields["category"]
        if c not in ALLOWABLE_CATEGORIES:
            raise ValueError(
                "The category must be one of: " + ", ".join(ALLOWABLE_CATEGORIES) + "."
            )
        out["category"] = c
    if "alt_questions" in fields:
        raw = fields["alt_questions"] or []
        if not isinstance(raw, list) or len(raw) > MAX_ALT_QUESTIONS:
            raise ValueError(f"At most {MAX_ALT_QUESTIONS} other wordings.")
        alts = []
        for a in raw:
            a = _clean(a)
            if len(a) > MAX_QUESTION:
                raise ValueError(f"Each other wording must be at most {MAX_QUESTION} characters.")
            if a and a not in alts:
                alts.append(a)
        out["alt_questions"] = alts
    for t in out.get("alt_questions") or []:
        if (c := pq.classify(t).category) in LOCKED_CATEGORIES:
            raise ValueError(
                f"“{t}” reads as a {pq.CATEGORY_LABELS[c].lower()} question. "
                "The agent never answers those; a nurse always does."
            )
    for key in ("question", "answer", "alt_questions"):
        texts = out.get(key)
        for t in texts if isinstance(texts, list) else [texts] if texts else []:
            if found := phi_problems(t):
                raise PhiError(
                    f"The {key.replace('_', ' ')} looks like it holds patient details "
                    f"({', '.join(found)}). The knowledge base is for clinic material only."
                )
    return out


def check_topic(question: str, category: str) -> None:
    """Refuse an item whose question the classifier files under another topic:
    patients asking it that way would be classified there, so the item would
    never be a candidate. Medication and symptom are named as never answered."""
    c = pq.classify(question).category
    if c == category:
        return
    label = pq.CATEGORY_LABELS.get(c, "Other").lower()
    if c in LOCKED_CATEGORIES:
        raise ValueError(
            f"This question reads as a {label} question. "
            "The agent never answers those; a nurse always does."
        )
    if c not in ALLOWABLE_CATEGORIES:
        raise ValueError(
            "The agent can't tell what topic this question is about, so it would never "
            "be used. Reword it with the key words a patient would say."
        )
    raise ValueError(
        f"This question reads as {label}, not "
        f"{pq.CATEGORY_LABELS.get(category, category).lower()}. "
        f"Choose {pq.CATEGORY_LABELS[c]} as the category, or reword it."
    )


class PhiError(ValueError):
    """Text that looks like it holds patient details."""


# --------------------------------------------------------------------------- lifecycle

CONTENT_FIELDS = ("question", "answer", "category", "alt_questions")


def new_item(item_id: str, fields: Mapping[str, Any], *, by: str, at: str) -> dict[str, Any]:
    """A new draft at version 1. ``fields`` must already be clean."""
    return {
        "item_id": item_id,
        "question": fields["question"],
        "alt_questions": list(fields.get("alt_questions") or []),
        "answer": fields["answer"],
        "category": fields["category"],
        "status": DRAFT,
        "version": 1,
        "created_at": at,
        "created_by": by,
        "updated_at": at,
        "updated_by": by,
        "approved_at": None,
        "approved_by": None,
    }


def edit_item(
    doc: Mapping[str, Any], fields: Mapping[str, Any], *, by: str, at: str
) -> dict[str, Any] | None:
    """Apply clean ``fields``. None when nothing actually changes.

    Changing the content of an approved (or retired) item makes a new version and
    puts it back to draft: it needs approving again before the agent can say it.
    """
    changed = {k: v for k, v in fields.items() if k in CONTENT_FIELDS and doc.get(k) != v}
    if not changed:
        return None
    out = {**doc, **changed, "updated_at": at, "updated_by": by}
    if doc.get("status") != DRAFT:
        out["version"] = int(doc.get("version") or 1) + 1
        out["status"] = DRAFT
        out["approved_at"] = None
        out["approved_by"] = None
    return out


def approve_item(doc: Mapping[str, Any], *, by: str, at: str) -> dict[str, Any] | None:
    """Draft or retired -> approved. None when it is already approved."""
    if doc.get("status") == APPROVED:
        return None
    return {
        **doc,
        "status": APPROVED,
        "approved_at": at,
        "approved_by": by,
        "updated_at": at,
        "updated_by": by,
    }


def retire_item(doc: Mapping[str, Any], *, by: str, at: str) -> dict[str, Any] | None:
    """-> retired (never answered again). None when it is already retired."""
    if doc.get("status") == RETIRED:
        return None
    return {**doc, "status": RETIRED, "updated_at": at, "updated_by": by}


ITEM_KEYS = (
    "item_id",
    "question",
    "alt_questions",
    "answer",
    "category",
    "category_label",
    "status",
    "version",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "approved_at",
    "approved_by",
)


def public_item(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The API's Item shape."""
    out = {k: doc.get(k) for k in ITEM_KEYS}
    out["alt_questions"] = list(doc.get("alt_questions") or [])
    out["category_label"] = pq.CATEGORY_LABELS.get(doc.get("category") or "", "Other")
    return out


# --------------------------------------------------------------------------- settings


def normalize_settings(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Stored settings merged over the defaults, with anything unsafe dropped.

    Medication, symptom and other are removed from ``allowed_categories`` here
    even if someone wrote them into Firestore directly (the gate in
    :func:`answer` refuses them as well).
    """
    d = dict(doc or {})
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    out["allowed_categories"] = list(DEFAULT_SETTINGS["allowed_categories"])
    if isinstance(d.get("enabled"), bool):
        out["enabled"] = d["enabled"]
    if isinstance(d.get("channels"), dict):
        for ch in CHANNELS:
            if isinstance(d["channels"].get(ch), bool):
                out["channels"][ch] = d["channels"][ch]
    if isinstance(d.get("allowed_categories"), list):
        out["allowed_categories"] = [
            c for c in ALLOWABLE_CATEGORIES if c in d["allowed_categories"]
        ]
    mm = d.get("min_match")
    if isinstance(mm, int | float) and not isinstance(mm, bool):
        out["min_match"] = min(max(float(mm), MIN_MATCH_RANGE[0]), MIN_MATCH_RANGE[1])
    if isinstance(d.get("ask_satisfied"), bool):
        out["ask_satisfied"] = d["ask_satisfied"]
    return out


def applies(settings: Mapping[str, Any], channel: str) -> bool:
    """Whether the agent answers from the knowledge base on ``channel`` now."""
    return bool(settings.get("enabled")) and bool((settings.get("channels") or {}).get(channel))


_settings_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_items_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


async def load_settings(store, *, fresh: bool = False) -> dict[str, Any]:
    """``settings/knowledge``, normalised, through a 60 s cache. A read failure
    keeps the last value, else the defaults (off): never fail a call over this."""
    hit = _settings_cache.get(id(store))
    if not fresh and hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        s = normalize_settings(await store.get(SETTINGS_COLLECTION, SETTINGS_DOC))
    except Exception:
        log.exception("knowledge settings read failed", extra={"event": "knowledge.settings_err"})
        s = hit[1] if hit else normalize_settings(None)
    _settings_cache[id(store)] = (time.monotonic(), s)
    return s


async def load_approved(store, *, fresh: bool = False) -> list[dict[str, Any]]:
    """The approved items, through a 60 s cache (a read failure keeps the last list,
    else none: every question then defers)."""
    from .store import Where

    hit = _items_cache.get(id(store))
    if not fresh and hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        rows = await store.query(ITEMS, [Where("status", "==", APPROVED)])
        items = [{**d, "item_id": d.get("item_id") or i} for i, d in rows]
    except Exception:
        log.exception("knowledge items read failed", extra={"event": "knowledge.items_err"})
        items = hit[1] if hit else []
    _items_cache[id(store)] = (time.monotonic(), items)
    return items


def forget(store) -> None:
    _settings_cache.pop(id(store), None)
    _items_cache.pop(id(store), None)


# --------------------------------------------------------------------------- answering


@dataclass(frozen=True)
class Candidate:
    """What the matcher may see of an item: its id and its wordings. Never the answer."""

    item_id: str
    question: str
    alt_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    item_id: str | None
    confidence: float


#: ``await matcher(question, candidates) -> Match``.
Matcher = Callable[[str, Sequence[Candidate]], Awaitable[Match]]


@dataclass(frozen=True)
class KnowledgeResult:
    outcome: str  # "answered" | "deferred"
    category: str
    reason: str
    answer: str | None = None
    item_id: str | None = None
    item_version: int | None = None
    confidence: float | None = None

    @property
    def answered(self) -> bool:
        return self.outcome == pq.OUTCOME_ANSWERED

    @property
    def category_label(self) -> str:
        return pq.CATEGORY_LABELS.get(self.category, "Other")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "outcome": self.outcome,
            "category": self.category,
            "category_label": self.category_label,
            "reason": self.reason,
        }
        for k in ("answer", "item_id", "item_version", "confidence"):
            if (v := getattr(self, k)) is not None:
                out[k] = v
        return out


def _defer(category: str, reason: str, confidence: float | None = None) -> KnowledgeResult:
    return KnowledgeResult(pq.OUTCOME_DEFERRED, category, reason, confidence=confidence)


async def answer(
    question: str,
    *,
    items: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    matcher: Matcher | None,
    timeout_s: float = MATCH_TIMEOUT_S,
) -> KnowledgeResult:
    """Answer ``question`` from the approved ``items``, or defer. See the module doc.

    ``settings`` are used for the category list and threshold only: whether the
    feature is on for a channel (:func:`applies`) is the caller's decision, so the
    admin "Try it" can run this with grounding off.
    """
    q = " ".join((question or "").split())[: pq.MAX_QUESTION_CHARS]
    category = pq.classify(q).category
    if not q:
        return _defer(category, R_EMPTY)
    from .intent import is_red_flag

    if is_red_flag(q):
        return _defer(category, R_RED_FLAG)
    if category in LOCKED_CATEGORIES:
        return _defer(category, R_LOCKED)
    if category not in ALLOWABLE_CATEGORIES:
        return _defer(category, R_OTHER)
    allowed = normalize_settings(settings)["allowed_categories"]
    if category not in allowed:
        return _defer(category, R_NOT_ALLOWED)
    min_match = normalize_settings(settings)["min_match"]

    by_id = {
        str(it.get("item_id")): it
        for it in items
        if it.get("status") == APPROVED
        and it.get("category") == category
        and it.get("item_id")
        and str(it.get("answer") or "").strip()
    }
    if not by_id or matcher is None:
        return _defer(category, R_NO_CANDIDATES)
    candidates = [
        Candidate(
            item_id=i,
            question=str(it.get("question") or ""),
            alt_questions=tuple(str(a) for a in it.get("alt_questions") or []),
        )
        for i, it in by_id.items()
    ]
    try:
        match = await asyncio.wait_for(matcher(q, candidates), timeout_s)
    except TimeoutError:
        log.warning("knowledge match timed out", extra={"event": "knowledge.timeout"})
        return _defer(category, R_TIMEOUT)
    except Exception:
        log.exception("knowledge match failed", extra={"event": "knowledge.error"})
        return _defer(category, R_ERROR)
    try:
        conf = float(match.confidence)
    except (TypeError, ValueError, AttributeError):
        return _defer(category, R_ERROR)
    conf = min(max(conf, 0.0), 1.0)
    picked = getattr(match, "item_id", None)
    if not picked or str(picked) not in by_id:
        return _defer(category, R_NO_MATCH, conf)
    if conf < min_match:
        return _defer(category, R_LOW, conf)
    item = by_id[str(picked)]
    return KnowledgeResult(
        pq.OUTCOME_ANSWERED,
        category,
        R_MATCHED,
        answer=str(item["answer"]).strip(),
        item_id=str(picked),
        item_version=int(item.get("version") or 1),
        confidence=conf,
    )


# --------------------------------------------------------------------------- offline matcher

_STOP = frozenset(
    """a an the i my me we our you your to of for on in at is it its be am are was do does
    did can could should would will when where what which who how how's what's whats there
    any some more again yet ok okay after before with and or if this that get go allowed
    much soon long until about have has need""".split()
)


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z']+", (text or "").lower()):
        w = w.strip("'").removesuffix("'s")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in _STOP:
            out.add(w)
    return out


async def keyword_matcher(question: str, candidates: Sequence[Candidate]) -> Match:
    """A deterministic, offline matcher (tests, the question test set, local dev):
    the share of a candidate wording's content words found in the question."""
    asked = _words(question)
    best: tuple[float, str | None] = (0.0, None)
    for c in candidates:
        for wording in (c.question, *c.alt_questions):
            want = _words(wording)
            if want:
                score = len(want & asked) / len(want)
                if score > best[0]:
                    best = (score, c.item_id)
    return Match(item_id=best[1], confidence=best[0])
