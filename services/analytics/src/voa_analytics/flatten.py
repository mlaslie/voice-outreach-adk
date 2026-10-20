"""Firestore call docs -> de-identified analytics rows. Pure functions only.

This module is the de-identification boundary: whatever it returns goes to
BigQuery, where a wider audience than the nurses can read it. So it works by
ALLOW-LIST: every output column is computed from a named field, enumerations are
checked against a small token pattern, and nothing is ever copied wholesale.
Never present in any row: names, phone numbers, dates of birth, the raw patient
id, transcripts, question text, survey free text, the reason sentence, or a
nurse's own words.

* ``patient_key`` is HMAC-SHA256(key, patient_id): stable across exports (so a
  patient's attempts can be counted) but not reversible without the key, which
  lives in Secret Manager and never in BigQuery.
* ``fact_reasons`` keeps only reasons whose ``source`` is ``triage``: those are
  fixed phrases written by ``voa_core.survey.triage`` ("fever reported", "pain
  8/10"). Agent and nurse reasons can hold free text and are dropped.
* ``fact_questions`` keeps a question's category and priority, never its words.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from voa_core import patient_questions as pq
from voa_core.review import CLOSED_STATUSES, TIER_RANK
from voa_core.survey import swelling_is_urgent

Row = dict[str, Any]

#: Enumerations and ids must look like this, or they are dropped. A sentence, a
#: phone number with "+" or spaces, an e-mail address: none of them pass.
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: The timestamp suffix of a report doc id: {patient_id}_{YYYYmmdd_HHMMSS_ffffff}.
_REPORT_TS = re.compile(r"_(\d{8}_\d{6}_\d{6})$")
#: Generic text (triage phrases, the text-skip reason) is kept only when it holds
#: at most this many digits: a phone number, a DOB or an MRN never fits.
MAX_DIGITS_IN_TEXT = 3
MAX_REASON_CHARS = 200
MAX_SMS_REASON_CHARS = 300

YES_NO = frozenset({"yes", "no", "unknown"})
PAIN_TRENDS = frozenset({"better", "same", "worse"})
SUGAR = frozenset({"high", "low", "in_range", "unknown"})
TIERS = frozenset(TIER_RANK)
TEST_PLACED_VIA = frozenset({"test_call", "sms_simulator"})


@dataclass
class Flattened:
    calls: list[Row] = field(default_factory=list)
    reasons: list[Row] = field(default_factory=list)
    questions: list[Row] = field(default_factory=list)
    skipped: int = 0  # docs with no usable id or placement time


# --------------------------------------------------------------------------- small helpers


def patient_key(key: bytes, patient_id: Any) -> str | None:
    if not isinstance(patient_id, str) or not patient_id.strip() or patient_id == "unknown":
        return None
    return hmac.new(key, patient_id.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def token(value: Any) -> str | None:
    """An enumeration or id value, or None when it does not look like one."""
    if isinstance(value, str) and _TOKEN.match(value):
        return value
    return None


def enum_token(value: Any) -> str | None:
    """An enumeration value (status, outcome, verdict, ...): a token with at most
    MAX_DIGITS_IN_TEXT digits, so a bare phone number or MRN never passes."""
    v = token(value)
    return v if v and sum(ch.isdigit() for ch in v) <= MAX_DIGITS_IN_TEXT else None


def generic_text(value: Any, max_chars: int) -> str | None:
    """Short, generic text (no more than MAX_DIGITS_IN_TEXT digits), else None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > max_chars or "@" in text:
        return None
    if sum(ch.isdigit() for ch in text) > MAX_DIGITS_IN_TEXT:
        return None
    return text


def parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def local_day(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


def _report_time(doc: Mapping[str, Any]) -> datetime | None:
    m = _REPORT_TS.search(str(doc.get("report_doc_id") or ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S_%f").replace(tzinfo=UTC)
    except ValueError:
        return None


def _yes_no(value: Any) -> str | None:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value if isinstance(value, str) and value in YES_NO else None


def _int(value: Any, lo: int | None = None, hi: int | None = None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        return None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return None
    return value


def override_direction(agent_tier: str | None, final_tier: str | None) -> str | None:
    """up / down / same: did the nurse make the call more or less severe?"""
    if agent_tier not in TIER_RANK or final_tier not in TIER_RANK:
        return None
    a, f = TIER_RANK[agent_tier], TIER_RANK[final_tier]
    return "up" if f > a else "down" if f < a else "same"


# --------------------------------------------------------------------------- timeline


def timeline_metrics(events_doc: Mapping[str, Any] | None) -> Row:
    """Counts and latencies from the gateway's ``call_events`` timeline.

    Every value is None when the call has no timeline (SMS-only, not answered, or
    a call from before timelines were kept): "not measured" is not "zero".
    """
    tl = [e for e in (events_doc or {}).get("timeline") or [] if isinstance(e, Mapping)]
    if not tl:
        return {
            "first_audio_ms": None,
            "interruptions": None,
            "tool_calls": None,
            "live_errors": None,
            "voicemail_played": None,
        }

    def first(name: str) -> int | float | None:
        return next(
            (
                e["ms"]
                for e in tl
                if e.get("event") == name and isinstance(e.get("ms"), int | float)
            ),
            None,
        )

    def count(pred) -> int:
        return sum(1 for e in tl if pred(str(e.get("event") or "")))

    start, audio = first("media.start"), first("agent.audio_start")
    return {
        "first_audio_ms": (
            round(audio - start) if audio is not None and start is not None else None
        ),
        "interruptions": count(lambda n: n == "agent.interrupted"),
        "tool_calls": count(lambda n: n == "tool.call"),
        "live_errors": count(lambda n: n.startswith("live.")),
        "voicemail_played": count(lambda n: n == "vm.play") > 0,
    }


# --------------------------------------------------------------------------- one call


def _questions(survey: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    """(category, priority) per patient question. The text is never read."""
    out: list[tuple[str, str | None]] = []
    qs = survey.get("patient_questions")
    for q in qs if isinstance(qs, list) else []:
        if not isinstance(q, Mapping):
            continue
        cat = q.get("category")
        pri = q.get("priority")
        out.append(
            (
                cat if cat in pq.CATEGORY_LABELS else pq.CAT_OTHER,
                pri if pri in pq.PRIORITIES else None,
            )
        )
    return out


def flatten_call(
    call_id: str,
    doc: Mapping[str, Any],
    events_doc: Mapping[str, Any] | None,
    *,
    key: bytes,
    tz: ZoneInfo,
) -> tuple[Row, list[Row], list[Row]] | None:
    """(fact_calls row, fact_reasons rows, fact_questions rows), or None when the
    doc has no usable id or placement time."""
    cid = token(call_id)
    initiated = parse_ts(doc.get("initiated_at"))
    if not cid or initiated is None:
        return None
    day = local_day(initiated, tz).isoformat()

    survey = doc.get("survey_responses")
    survey = survey if isinstance(survey, Mapping) else {}
    ctx = doc.get("patient_context")
    ctx = ctx if isinstance(ctx, Mapping) else {}

    follow_up = doc.get("follow_up") if doc.get("follow_up") in TIERS else None
    overridden = bool(doc.get("follow_up_override"))
    agent_tier = doc.get("agent_follow_up") if overridden else follow_up
    agent_tier = agent_tier if agent_tier in TIERS else follow_up

    review = doc.get("review") if isinstance(doc.get("review"), Mapping) else {}
    review_status = enum_token(review.get("status"))
    signed_off_at = parse_ts(review.get("at")) if review_status == "signed_off" else None
    minutes = None
    if signed_off_at:
        # The clock starts when the nurse could first act: the later of the call
        # ending and the report being filed (a by-text report lands hours later).
        starts = [t for t in (parse_ts(doc.get("ended_at")), _report_time(doc)) if t]
        if starts:
            minutes = round(max(0.0, (signed_off_at - max(starts)).total_seconds() / 60), 2)

    sms = doc.get("sms_follow_up") if isinstance(doc.get("sms_follow_up"), Mapping) else {}

    questions = _questions(survey)
    pain = _int(survey.get("pain_severity"), 0, 10)
    if pain is None and survey.get("pain_present") in ("no", False):
        pain = 0
    swelling = _yes_no(survey.get("legs_swollen"))
    placed_via = enum_token(doc.get("placed_via"))
    channel = doc.get("channel") if doc.get("channel") in ("voice", "sms") else "voice"

    row: Row = {
        "call_id": cid,
        "patient_key": patient_key(key, doc.get("patient_id")),
        "entry_id": token(doc.get("entry_id")),
        "source": enum_token(doc.get("source") or ctx.get("source")),
        "placed_via": placed_via,
        "channel": channel,
        "is_test": bool(doc.get("test")) or placed_via in TEST_PLACED_VIA,
        "day": day,
        "initiated_at": iso(initiated),
        "duration_s": _int(doc.get("duration_seconds"), 0),
        "call_status": enum_token(doc.get("call_status")),
        "outcome": enum_token(doc.get("outcome")),
        "answered": bool(doc.get("answered_at")),
        "answered_by": enum_token(doc.get("answered_by")),
        "dob_result": enum_token(doc.get("dob_verification_result")),
        "follow_up": follow_up,
        "agent_follow_up": agent_tier,
        "overridden": overridden,
        "override_direction": override_direction(agent_tier, follow_up) if overridden else None,
        "review_status": review_status,
        "signed_off_at": iso(signed_off_at),
        "minutes_to_signoff": minutes,
        "sms_follow_up_status": enum_token(sms.get("status")),
        "sms_follow_up_reason": generic_text(sms.get("reason"), MAX_SMS_REASON_CHARS),
        "has_questions": bool(questions) or _yes_no(survey.get("has_questions")) == "yes",
        "questions_immediate": sum(1 for _, p in questions if p == pq.PRIORITY_IMMEDIATE),
        "questions_next_day": sum(1 for _, p in questions if p == pq.PRIORITY_NEXT_DAY),
        "pain_score": pain,
        "pain_trend": survey.get("pain_trend") if survey.get("pain_trend") in PAIN_TRENDS else None,
        "fever": _yes_no(survey.get("fever_reported")),
        "incision": _yes_no(survey.get("incision_concerns")),
        "swelling": swelling,
        "swelling_urgent": swelling == "yes"
        and swelling_is_urgent(str(survey.get("swelling_description") or "")),
        "sleeping_well": _yes_no(survey.get("sleeping_well")),
        "sugar_status": (
            survey.get("blood_sugar_status") if survey.get("blood_sugar_status") in SUGAR else None
        ),
        **timeline_metrics(events_doc),
    }

    reasons: list[Row] = []
    raw_reasons = doc.get("follow_up_reasons")
    for r in raw_reasons if isinstance(raw_reasons, list) else []:
        if not isinstance(r, Mapping) or r.get("source", "triage") != "triage":
            continue
        text = generic_text(r.get("text"), MAX_REASON_CHARS)
        if text and r.get("tier") in TIERS:
            reasons.append({"call_id": cid, "day": day, "tier": r["tier"], "text": text})

    qrows = [
        {"call_id": cid, "day": day, "category": cat, "priority": pri} for cat, pri in questions
    ]
    return row, reasons, qrows


def flatten(
    calls: Iterable[tuple[str, Mapping[str, Any]]],
    events: Mapping[str, Mapping[str, Any]] | None,
    *,
    key: bytes,
    tz: str | ZoneInfo,
) -> Flattened:
    """Every call doc -> the three fact tables. ``events`` maps call id to its
    ``call_events`` doc (missing for calls with no timeline)."""
    if not key:
        raise ValueError("an HMAC key is required")
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    out = Flattened()
    for call_id, doc in calls:
        res = flatten_call(call_id, doc or {}, (events or {}).get(call_id), key=key, tz=zone)
        if res is None:
            out.skipped += 1
            continue
        row, reasons, questions = res
        out.calls.append(row)
        out.reasons.extend(reasons)
        out.questions.extend(questions)
    return out


def is_closed(review_status: str | None) -> bool:
    return review_status in CLOSED_STATUSES
