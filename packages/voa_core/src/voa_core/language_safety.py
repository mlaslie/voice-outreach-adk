"""The post-call re-check on the English translation (docs/LANGUAGES-PLAN.md §3.3,
step 3). Pure functions: the API task translates and calls these with ``now``.

After a call's transcript is translated, today's English checks run again on the
English of what the patient said:

* ``intent.red_flag_match`` on every patient turn (emergency);
* the free-text checks (swelling, severe language, asks for a person, worry) on
  the patient turns and on the translated free-text answers (same day, as in the
  post-op spec);
* ``patient_questions.classify`` on each translated patient question.

**Raise only.** A finding more urgent than the call's ``follow_up`` raises it and
adds a ``follow_up_reasons`` entry with ``source: "translation_check"``. Nothing is
ever lowered or cleared. A signed-off (or superseded) review is reopened the way a
nurse's tier change reopens it, and its EHR export is withdrawn. The result is
stored as ``translation_check: {status, at, findings, raised_to, from, reopened}``;
a call that has one is never re-checked (idempotent).

Only calls in which the patient spoke another language are re-checked. An all-
English call keeps exactly the triage it had: the live model heard it, the tools
typed it, and nothing here would add information, only transcript noise.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import checks, intent, languages, review, translate
from . import patient_questions as pq
from .parsers import _negated_words

SOURCE = "translation_check"
#: A call whose translation_check has one of these is never re-checked. "failed"
#: (the transcript could not be translated) is re-checked on a later delivery.
FINAL_STATUSES = frozenset({"done", "partial"})
#: Where a by-text "couldn't read this reply" raise is recorded (kept apart from
#: the post-call ``translation_check`` so it never blocks that re-check).
TEXT_CHECK = "text_translation_check"
CHECK_KEYS = ("translation_check", TEXT_CHECK)
MAX_REASON_CHARS = 200
MAX_REASONS = 20
URGENT = "urgent_same_day"
EMERGENCY = "emergency"

_SWELLING_WORDS = re.compile(r"\b(swell|swollen|swelling)", re.I)


@dataclass(frozen=True)
class Finding:
    tier: str
    #: red_flag, swelling, severe_language, asks_for_a_person, expressed_worry, question
    kind: str
    text: str  # the reason shown to nurses
    where: str  # "transcript", "answer <field>", "question <n>"

    def to_dict(self) -> dict[str, str]:
        return {"tier": self.tier, "kind": self.kind, "text": self.text, "where": self.where}


def rank(tier: str | None) -> int:
    return review.TIER_RANK.get(tier or "none", 0)


# --------------------------------------------------------------------------- what to check


def _foreign_lang(lang: Any) -> bool:
    return bool(lang) and not translate.is_english(str(lang))


def spoke_other_language(turns: Sequence[Mapping[str, Any]], call: Mapping[str, Any]) -> bool:
    """Whether the patient spoke (or is listed in) a language other than English:
    a translated patient turn, or a non-English ``language_used`` / ``language``."""
    for t in turns:
        if t.get("role") == "patient" and (t.get("text_en") or _foreign_lang(t.get("lang"))):
            return True
    for key in ("language_used", "language"):
        code = languages.normalize(call.get(key))
        if code and code not in languages.ENGLISH:
            return True
    return False


def original_findings(original: str, where: str) -> list[Finding]:
    """The per-language red-flag lists (Spanish today) on the patient's own words."""
    rf = intent.red_flag_match(original)
    if not rf:
        return []
    return [Finding(EMERGENCY, "red_flag", _cut(f"red flag found on {where}: {rf}"), where)]


def transcript_findings(turns: Sequence[Mapping[str, Any]]) -> list[Finding]:
    """Every patient turn: today's checks on its English, and the per-language
    red-flag lists on its original when the English showed no red flag."""
    out: list[Finding] = []
    for t in turns:
        if t.get("role") != "patient":
            continue
        original = str(t.get("text") or "").strip()
        english = str(t.get("text_en") or original).strip()
        found = english_findings(english, "translated transcript")
        if t.get("text_en") and not any(f.kind == "red_flag" for f in found):
            found += original_findings(original, "transcript")
        out += found
    return out


def answer_texts(call: Mapping[str, Any], campaign) -> dict[str, str]:
    """field -> the patient's own words to re-check: the free-text answers and
    every raw reply (``survey_responses._raw``), excluding the open-question
    field (its questions are re-checked one by one)."""
    sr = call.get("survey_responses") or {}
    if not isinstance(sr, Mapping):
        return {}
    skip = set(campaign.open_question_ids) if campaign is not None else set()
    out: dict[str, str] = {}
    fields = checks.prose_fields(campaign) if campaign is not None else ()
    for f in fields:
        v = sr.get(f)
        if isinstance(v, str) and v.strip():
            out[f] = v.strip()
    raw = sr.get(checks.RAW_KEY)
    if isinstance(raw, Mapping):
        for f, v in raw.items():
            if f in skip or not isinstance(v, str) or not v.strip():
                continue
            out.setdefault(f"_raw.{f}", v.strip())
    return out


def swelling_fields(campaign) -> frozenset[str]:
    """The answers the spec's swelling rule reads (post-op: swelling_description)."""
    out: set[str] = set()
    for rule in getattr(campaign, "rules", ()) or ():
        if getattr(rule, "check", None) == "swelling_is_urgent":
            out.update(getattr(rule, "args", ()) or ())
    return frozenset(out)


def questions_of(call: Mapping[str, Any]) -> list[dict[str, Any]]:
    sr = call.get("survey_responses") or {}
    qs = sr.get(pq_key()) if isinstance(sr, Mapping) else None
    return [dict(q) for q in qs] if isinstance(qs, list) else []


def pq_key() -> str:
    return checks.PATIENT_QUESTIONS


# --------------------------------------------------------------------------- the checks


def _negation_aware(pattern: re.Pattern[str], text: str) -> bool:
    """A match whose words are not negated ("not worried" is not worry)."""
    negated = _negated_words(text)
    for m in pattern.finditer(text or ""):
        words = m.group(0).lower().split()
        if not any(n.startswith(w) for w in words for n in negated):
            return True
    return False


_PROSE: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("severe_language", checks.SEVERE_LANGUAGE, "severe pain described"),
    ("asks_for_a_person", checks.WANTS_HUMAN, "asked to speak to someone"),
    ("expressed_worry", checks.WORRIED, "expressed worry"),
)


def _cut(text: str) -> str:
    return text[:MAX_REASON_CHARS]


def english_findings(english: str, where: str, *, swelling: bool = False) -> list[Finding]:
    """Today's English checks on one English text. ``where`` names it in the
    reason ("translated transcript", "translated answer")."""
    out: list[Finding] = []
    text = english or ""
    if not text.strip():
        return out
    rf = intent.red_flag_match(text, translated=True)
    if rf:
        out.append(Finding(EMERGENCY, "red_flag", _cut(f"red flag found on {where}: {rf}"), where))
    if (swelling or _SWELLING_WORDS.search(text)) and checks.swelling_is_urgent(text):
        out.append(
            Finding(
                URGENT,
                "swelling",
                _cut(f"leg or foot swelling (one side, worsening, or painful) found on {where}"),
                where,
            )
        )
    for kind, pattern, label in _PROSE:
        if _negation_aware(pattern, text):
            out.append(Finding(URGENT, kind, _cut(f"{label} found on {where}"), where))
    return out


def question_findings(
    questions: list[dict[str, Any]], english: Mapping[int, str]
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """(the questions with ``text_en`` added and re-classified on the English, the
    findings). The more urgent priority is kept; the category is the English one."""
    out = [dict(q) for q in questions]
    found: list[Finding] = []
    for i, en in english.items():
        if not (0 <= i < len(out)) or not en:
            continue
        q = out[i]
        q.setdefault("text_original", q.get("text"))
        q["text_en"] = en[: pq.MAX_QUESTION_CHARS]
        c = pq.classify(en)
        before = q.get("priority")
        q["priority"] = pq.more_urgent(str(before or pq.PRIORITY_IMMEDIATE), c.priority)
        if c.category != pq.CAT_OTHER or q.get("category") not in pq.CATEGORY_LABELS:
            q["category"] = c.category
        where = f"question {i + 1}"
        rf = intent.red_flag_match(en, translated=True)
        if rf:
            found.append(
                Finding(
                    EMERGENCY,
                    "red_flag",
                    _cut(f"red flag found on translated {where}: {rf}"),
                    where,
                )
            )
        if c.priority == pq.PRIORITY_IMMEDIATE and pq.needs_follow_up(q):
            label = pq.CATEGORY_LABELS.get(c.category, "Other").lower()
            found.append(
                Finding(
                    URGENT,
                    "question",
                    _cut(f"question asked ({label}) found on translated {where}"),
                    where,
                )
            )
    return out, found


# --------------------------------------------------------------------------- the patch


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: dict[tuple[str, str], Finding] = {}
    for f in findings:
        seen.setdefault((f.tier, f.text), f)
    return sorted(seen.values(), key=lambda f: -rank(f.tier))


def apply(
    existing: Mapping[str, Any],
    findings: Sequence[Finding],
    *,
    now: str,
    questions: list[dict[str, Any]] | None = None,
    status: str = "done",
    note: str | None = None,
    check_key: str = "translation_check",
) -> tuple[dict[str, Any], bool]:
    """(the new call doc, withdraw the EHR export?). Raise only.

    ``questions``: the re-classified patient questions to store (None: unchanged).
    A finding more urgent than ``follow_up`` raises the tier; a closed review is
    then reopened (its export withdrawn) exactly like a nurse's tier change."""
    doc: dict[str, Any] = dict(existing)
    found = _dedupe(findings)
    current = existing.get("follow_up") or "none"
    top = found[0] if found else None
    raised_to = top.tier if top is not None and rank(top.tier) > rank(current) else None
    check: dict[str, Any] = {
        "status": status,
        "at": now,
        "findings": [f.to_dict() for f in found],
        "from": current,
        "raised_to": raised_to,
        "reopened": False,
        # Whether this check ever raised the tier: a later, milder report must not
        # lower it (review.merge_late_report holds it, like a nurse's tier).
        "held": bool(
            raised_to
            or (existing.get(check_key) or {}).get("held")
            or (existing.get(check_key) or {}).get("raised_to")
        ),
    }
    if note:
        check["note"] = note[:MAX_REASON_CHARS]
    if questions is not None:
        sr = dict(existing.get("survey_responses") or {})
        sr[pq_key()] = questions
        doc["survey_responses"] = sr
    withdraw = False
    if raised_to is not None:
        new = [
            {"tier": f.tier, "text": f.text, "source": SOURCE, "fields": [], "at": now}
            for f in found
            if rank(f.tier) > rank(current)
        ]
        doc["follow_up"] = raised_to
        doc["follow_up_reasons"] = [*new, *(existing.get("follow_up_reasons") or [])][:MAX_REASONS]
        lead = "Found on the translated transcript: " + "; ".join(r["text"] for r in new) + "."
        old = existing.get("follow_up_reason")
        doc["follow_up_reason"] = f"{lead} {old}" if old else lead
        event = {
            "at": now,
            "from": current,
            "to": raised_to,
            "by": SOURCE,
            "reason": _cut(lead),
        }
        doc["follow_up_history"] = [*(existing.get("follow_up_history") or []), event]
        if existing.get("follow_up_override"):
            # The nurse's lower tier no longer describes the call (as for a late
            # report that escalates it, review.merge_late_report).
            doc["agent_follow_up"] = raised_to
            doc["follow_up_override"] = None
        rv = existing.get("review") or {}
        if rv.get("status") in review.CLOSED_STATUSES:
            reopened = {
                **rv,
                "status": "reopened",
                "reopened_at": now,
                "reopened_by": SOURCE,
                "reopened_because": _cut(
                    f"the translated transcript raised the follow-up to {raised_to}"
                ),
            }
            if review.has_export(rv):
                reopened["export_withdrawn"] = True
                withdraw = True
            doc["review"] = reopened
            doc["review_history"] = [*(existing.get("review_history") or []), reopened]
            check["reopened"] = True
    doc[check_key] = check
    doc["updated_at"] = now
    return doc, withdraw


def unreadable_finding(language: str | None, what: str = "answers") -> Finding:
    """The fail-safe when the translation needed for the re-check failed."""
    lang = languages.name(language) if language else "another language"
    return Finding(
        URGENT,
        "unreadable",
        _cut(f"couldn't read {what} in {lang}: a nurse must check"),
        "translation",
    )
