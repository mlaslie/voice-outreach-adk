"""The clinic's knowledge: storage, lifecycle, settings (docs/KNOWLEDGE-PLAN.md, v2).

What lives here is shared by every service and has no model in it:

* **Example Q&A** (``knowledge_items``) and **guidelines** (``knowledge_documents``):
  validation, the PHI check, and the lifecycle draft -> approved -> retired.
  Editing approved content makes a new draft version. Only approved content is
  ever used.
* **Settings** (``settings/knowledge``): ``mode`` off | shadow | live, channels,
  allowed categories, ``min_support``. Old documents (``enabled``) migrate on read.
* **Guideline applicability** to one patient's procedure and care setting.
* **Patient context** for the knowledge agent: procedure, days since surgery,
  care setting, diabetic. Never the name, DOB, phone or MRN.
* Cached loaders for the approved content.

Answering itself (classifier, drafting, grounding check) is the dedicated
``voa_knowledge`` package.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import patient_questions as pq

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- storage

ITEMS = "knowledge_items"
DOCUMENTS = "knowledge_documents"
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
#: Categories an admin may allow, and an example may be filed under.
ALLOWABLE_CATEGORIES: tuple[str, ...] = tuple(
    c for c in pq.CATEGORY_LABELS if c not in NEVER_ANSWERED
)

CARE_SETTINGS: tuple[str, ...] = ("inpatient", "outpatient")

# --------------------------------------------------------------------------- limits

MAX_QUESTION = 300
MAX_ANSWER = 600
MAX_ALT_QUESTIONS = 10
MAX_ITEMS = 500

MAX_TITLE = 120
MAX_BODY = 20_000
MAX_DOCUMENTS = 100
MAX_APPROVED_CHARS = 200_000
MAX_PROCEDURES = 20
MAX_PROCEDURE = 120

# --------------------------------------------------------------------------- settings

MODE_OFF, MODE_SHADOW, MODE_LIVE = "off", "shadow", "live"
MODES = (MODE_OFF, MODE_SHADOW, MODE_LIVE)
CHANNELS = ("voice", "sms")
DEFAULT_MIN_SUPPORT = 0.85
MIN_SUPPORT_RANGE = (0.5, 1.0)

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": MODE_OFF,
    "channels": {"voice": True, "sms": False},
    "allowed_categories": list(ALLOWABLE_CATEGORIES),
    "min_support": DEFAULT_MIN_SUPPORT,
    "ask_satisfied": True,
}

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


class PhiError(ValueError):
    """Text that looks like it holds patient details."""


def _check_phi(what: str, texts: list[str]) -> None:
    for t in texts:
        if found := phi_problems(t):
            raise PhiError(
                f"The {what} looks like it holds patient details "
                f"({', '.join(found)}). The knowledge base is for clinic material only."
            )


# --------------------------------------------------------------------------- example Q&A


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _clean_block(text: Any) -> str:
    """Whitespace tidied line by line; line breaks kept."""
    a = str(text or "").strip()
    return "\n".join(" ".join(line.split()) for line in a.splitlines()).strip()


def clean_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the example fields that are present in ``fields``
    (question, answer, category, alt_questions). Raises ValueError with a message
    an admin can act on.

    ``category`` is for filtering and display: it no longer has to match how a
    patient's question is classified. A wording that itself reads as a medication
    or symptom question is still refused: the agent never answers those."""
    out: dict[str, Any] = {}
    if "question" in fields:
        q = _clean(fields["question"])
        if not q:
            raise ValueError("The question is required.")
        if len(q) > MAX_QUESTION:
            raise ValueError(f"The question must be at most {MAX_QUESTION} characters.")
        out["question"] = q
    if "answer" in fields:
        a = _clean_block(fields["answer"])
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
    wordings = ([out["question"]] if "question" in out else []) + out.get("alt_questions", [])
    for t in wordings:
        if (c := pq.classify(t).category) in LOCKED_CATEGORIES:
            raise ValueError(
                f"“{t}” reads as a {pq.CATEGORY_LABELS[c].lower()} question. "
                "The agent never answers those; a nurse always does."
            )
    for key in ("question", "answer", "alt_questions"):
        texts = out.get(key)
        _check_phi(
            key.replace("_", " "),
            texts if isinstance(texts, list) else [texts] if texts else [],
        )
    return out


# --------------------------------------------------------------------------- guidelines


def clean_document_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the guideline fields present in ``fields`` (title,
    body, procedures, care_settings). Raises ValueError (PhiError for PHI)."""
    out: dict[str, Any] = {}
    if "title" in fields:
        t = _clean(fields["title"])
        if not t:
            raise ValueError("The title is required.")
        if len(t) > MAX_TITLE:
            raise ValueError(f"The title must be at most {MAX_TITLE} characters.")
        out["title"] = t
    if "body" in fields:
        b = _clean_block(fields["body"])
        if not b:
            raise ValueError("The text is required.")
        if len(b) > MAX_BODY:
            raise ValueError(f"The text must be at most {MAX_BODY:,} characters.")
        out["body"] = b
    if "procedures" in fields:
        raw = fields["procedures"] or []
        if not isinstance(raw, list) or len(raw) > MAX_PROCEDURES:
            raise ValueError(f"At most {MAX_PROCEDURES} procedures.")
        procs: list[str] = []
        for p in raw:
            p = _clean(p)
            if len(p) > MAX_PROCEDURE:
                raise ValueError(f"Each procedure must be at most {MAX_PROCEDURE} characters.")
            if p and p.lower() not in {x.lower() for x in procs}:
                procs.append(p)
        out["procedures"] = procs
    if "care_settings" in fields:
        raw = fields["care_settings"] or []
        if not isinstance(raw, list) or any(s not in CARE_SETTINGS for s in raw):
            raise ValueError("Care settings must be from: " + ", ".join(CARE_SETTINGS) + ".")
        out["care_settings"] = [s for s in CARE_SETTINGS if s in raw]
    _check_phi("title", [out["title"]] if "title" in out else [])
    _check_phi("text", [out["body"]] if "body" in out else [])
    _check_phi("procedures", out.get("procedures", []))
    return out


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def document_applies(
    doc: Mapping[str, Any], *, procedure: str | None, care_setting: str | None
) -> bool:
    """Whether a guideline applies to this patient.

    * ``procedures`` empty: every procedure. Otherwise one of them must match the
      patient's procedure, case-insensitively, as a substring either way. An
      unknown procedure matches only a guideline with no procedures.
    * ``care_settings`` empty: every setting. Otherwise the patient's setting must
      be listed; an unknown setting matches only a guideline with none.
    """
    procs = [_norm(p) for p in doc.get("procedures") or [] if _norm(p)]
    if procs:
        mine = _norm(procedure or "")
        if not mine or not any(p in mine or mine in p for p in procs):
            return False
    settings = [s for s in doc.get("care_settings") or [] if s in CARE_SETTINGS]
    return not settings or care_setting in settings


# --------------------------------------------------------------------------- lifecycle

CONTENT_FIELDS = ("question", "answer", "category", "alt_questions")
DOCUMENT_CONTENT_FIELDS = ("title", "body", "procedures", "care_settings")


def _audit_fields(by: str, at: str) -> dict[str, Any]:
    return {
        "status": DRAFT,
        "version": 1,
        "created_at": at,
        "created_by": by,
        "updated_at": at,
        "updated_by": by,
        "approved_at": None,
        "approved_by": None,
    }


def new_item(item_id: str, fields: Mapping[str, Any], *, by: str, at: str) -> dict[str, Any]:
    """A new draft example at version 1. ``fields`` must already be clean."""
    return {
        "item_id": item_id,
        "question": fields["question"],
        "alt_questions": list(fields.get("alt_questions") or []),
        "answer": fields["answer"],
        "category": fields["category"],
        **_audit_fields(by, at),
    }


def new_document(doc_id: str, fields: Mapping[str, Any], *, by: str, at: str) -> dict[str, Any]:
    """A new draft guideline at version 1. ``fields`` must already be clean."""
    return {
        "doc_id": doc_id,
        "title": fields["title"],
        "body": fields["body"],
        "procedures": list(fields.get("procedures") or []),
        "care_settings": list(fields.get("care_settings") or []),
        **_audit_fields(by, at),
    }


def edit_item(
    doc: Mapping[str, Any],
    fields: Mapping[str, Any],
    *,
    by: str,
    at: str,
    content_fields: tuple[str, ...] = CONTENT_FIELDS,
) -> dict[str, Any] | None:
    """Apply clean ``fields``. None when nothing actually changes.

    Changing the content of an approved (or retired) entry makes a new version and
    puts it back to draft: it needs approving again before the agent can use it.
    Works for examples and (with ``DOCUMENT_CONTENT_FIELDS``) guidelines.
    """
    changed = {k: v for k, v in fields.items() if k in content_fields and doc.get(k) != v}
    if not changed:
        return None
    out = {**doc, **changed, "updated_at": at, "updated_by": by}
    if doc.get("status") != DRAFT:
        out["version"] = int(doc.get("version") or 1) + 1
        out["status"] = DRAFT
        out["approved_at"] = None
        out["approved_by"] = None
    return out


def edit_document(
    doc: Mapping[str, Any], fields: Mapping[str, Any], *, by: str, at: str
) -> dict[str, Any] | None:
    return edit_item(doc, fields, by=by, at=at, content_fields=DOCUMENT_CONTENT_FIELDS)


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
    """-> retired (never used again). None when it is already retired."""
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

DOCUMENT_KEYS = (
    "doc_id",
    "title",
    "body",
    "procedures",
    "care_settings",
    "status",
    "version",
    "chars",
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


def document_chars(doc: Mapping[str, Any]) -> int:
    return len(str(doc.get("body") or ""))


def public_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The API's Doc shape."""
    out = {k: doc.get(k) for k in DOCUMENT_KEYS}
    out["procedures"] = list(doc.get("procedures") or [])
    out["care_settings"] = list(doc.get("care_settings") or [])
    out["chars"] = document_chars(doc)
    return out


# --------------------------------------------------------------------------- settings


def normalize_settings(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Stored settings merged over the defaults, with anything unsafe dropped.

    Migration on read: a v1 document's ``enabled: true`` reads as ``mode: "live"``,
    ``enabled: false`` as ``"off"``; ``min_match`` is dropped. Medication, symptom
    and other are removed from ``allowed_categories`` even if someone wrote them
    into Firestore directly (the agent's gate refuses them as well).
    """
    d = dict(doc or {})
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    out["allowed_categories"] = list(DEFAULT_SETTINGS["allowed_categories"])
    if d.get("mode") in MODES:
        out["mode"] = d["mode"]
    elif isinstance(d.get("enabled"), bool):
        out["mode"] = MODE_LIVE if d["enabled"] else MODE_OFF
    if isinstance(d.get("channels"), dict):
        for ch in CHANNELS:
            if isinstance(d["channels"].get(ch), bool):
                out["channels"][ch] = d["channels"][ch]
    if isinstance(d.get("allowed_categories"), list):
        out["allowed_categories"] = [
            c for c in ALLOWABLE_CATEGORIES if c in d["allowed_categories"]
        ]
    ms = d.get("min_support")
    if isinstance(ms, int | float) and not isinstance(ms, bool):
        out["min_support"] = min(max(float(ms), MIN_SUPPORT_RANGE[0]), MIN_SUPPORT_RANGE[1])
    if isinstance(d.get("ask_satisfied"), bool):
        out["ask_satisfied"] = d["ask_satisfied"]
    return out


def mode_for(settings: Mapping[str, Any], channel: str) -> str:
    """The mode on ``channel``: the settings' mode, or "off" when the channel is off."""
    s = normalize_settings(settings)
    return s["mode"] if s["channels"].get(channel) else MODE_OFF


def applies(settings: Mapping[str, Any], channel: str) -> bool:
    """Whether the agent answers patients from the knowledge base on ``channel``."""
    return mode_for(settings, channel) == MODE_LIVE


def shadows(settings: Mapping[str, Any], channel: str) -> bool:
    """Whether a filed report on ``channel`` gets the agent's shadow drafts."""
    return mode_for(settings, channel) == MODE_SHADOW


# --------------------------------------------------------------------------- patient context


def patient_context(patient: Mapping[str, Any] | None, *, today: date) -> dict[str, Any]:
    """What the knowledge agent may know about the patient: procedure, days since
    surgery, care setting, diabetic. Never the name, DOB, phone or MRN.

    Reads the dialler's patient fields (``operation_name``, ``operation_date``,
    ``care_setting``, ``is_diabetic``); unknown or malformed values are left out."""
    p = patient or {}
    out: dict[str, Any] = {}
    proc = _clean(p.get("operation_name") or p.get("procedure") or "")[:MAX_PROCEDURE]
    if proc:
        out["procedure"] = proc
    op = p.get("operation_date")
    if isinstance(op, str) and op:
        try:
            days = (today - date.fromisoformat(op[:10])).days
        except ValueError:
            days = None
        if days is not None and days >= 0:
            out["days_since_surgery"] = days
    if p.get("care_setting") in CARE_SETTINGS:
        out["care_setting"] = p["care_setting"]
    if isinstance(p.get("is_diabetic"), bool):
        out["is_diabetic"] = p["is_diabetic"]
    return out


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """The approved examples and guidelines at one moment."""

    examples: list[dict[str, Any]] = field(default_factory=list)
    guidelines: list[dict[str, Any]] = field(default_factory=list)


_settings_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_items_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_docs_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


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


async def _load_approved(store, collection: str, key: str, cache: dict, fresh: bool):
    from .store import Where

    hit = cache.get(id(store))
    if not fresh and hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        rows = await store.query(collection, [Where("status", "==", APPROVED)])
        out = [{**d, key: d.get(key) or i} for i, d in rows]
    except Exception:
        log.exception("knowledge read failed", extra={"event": "knowledge.read_err"})
        out = hit[1] if hit else []
    cache[id(store)] = (time.monotonic(), out)
    return out


async def load_approved(store, *, fresh: bool = False) -> list[dict[str, Any]]:
    """The approved examples, through a 60 s cache (a read failure keeps the last
    list, else none: every question then defers)."""
    return await _load_approved(store, ITEMS, "item_id", _items_cache, fresh)


async def load_approved_documents(store, *, fresh: bool = False) -> list[dict[str, Any]]:
    """The approved guidelines, through a 60 s cache (same failure rule)."""
    return await _load_approved(store, DOCUMENTS, "doc_id", _docs_cache, fresh)


async def load_snapshot(store, *, fresh: bool = False) -> KnowledgeSnapshot:
    return KnowledgeSnapshot(
        examples=await load_approved(store, fresh=fresh),
        guidelines=await load_approved_documents(store, fresh=fresh),
    )


def forget(store) -> None:
    _settings_cache.pop(id(store), None)
    _items_cache.pop(id(store), None)
    _docs_cache.pop(id(store), None)


# --------------------------------------------------------------------------- shadow mode

#: The API task that drafts shadow answers for a filed report's questions.
SHADOW_TASK_PATH = "/tasks/knowledge-shadow"


async def enqueue_shadow(
    store,
    tasks,
    queue: str,
    *,
    call_id: str | None,
    survey: Mapping[str, Any] | None,
    channel: str,
    report_id: str = "",
) -> bool:
    """After a report is filed: in shadow mode on ``channel``, queue the task that
    stores what the agent would have said for each question. Never raises (a
    report is never failed over this). True when a task was queued."""
    try:
        qs = (survey or {}).get("patient_questions")
        if not call_id or not isinstance(qs, list) or not qs:
            return False
        if not shadows(await load_settings(store), channel):
            return False
        return bool(
            await tasks.enqueue(
                queue,
                SHADOW_TASK_PATH,
                {"call_id": call_id},
                name=f"kshadow-{call_id}-{report_id}" if report_id else None,
            )
        )
    except Exception:
        log.exception("shadow enqueue failed", extra={"event": "knowledge.shadow_enqueue_err"})
        return False
