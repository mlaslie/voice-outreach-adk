"""Nurse review and sign-off rules (old proxy backlog A-1.6..A-1.8, B-5, B-6, B-7, D-1).

Pure functions only: the API reads the docs, calls these with an explicit ``now``
and writes what they return.

A completed assessment is not final until a nurse signs it off. Signing off a
call that needs follow-up IS resolving that follow-up, and needs the outcome of
the nurse's own callback as a note. Each sign-off produces the structured result
for the EHR outbox (``pending_export``); nothing reaches the outbox before
sign-off, and a later change to the call (a nurse re-tiering it, or a late report
filed onto it) reopens the review and withdraws that record.

Decisions this module encodes, each from an incident or review in the old proxy:

* D-1: a report that needed a callback stays open until a nurse closes it. A
  later CLEAN report does not close it; signing off the later one becomes a
  resolve that needs a note and closes the earlier one with that note.
* B-6: one patient is one MRN. A call with no MRN is matched by phone only to
  other calls with no MRN, never to a patient who has one.
* B-5: a report arriving on a call a nurse already handled reopens a closed
  review; a tier the nurse holds stands against a milder report, a more severe
  one escalates it.
* B-7: a sign-off is a clinical signature: it needs a verified signer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

Doc = dict[str, Any]

#: Tiers that need a nurse's callback (same as reports.ACTION_TIERS; not imported
#: from there because lifecycle imports this module and reports imports lifecycle).
NEEDS_TIERS: tuple[str, ...] = ("emergency", "urgent_same_day", "routine_next_business_day")

#: Severity, for "does a late report escalate the nurse's tier?".
TIER_RANK: Mapping[str, int] = {
    "emergency": 4,
    "urgent_same_day": 3,
    "routine_next_business_day": 2,
    "informational": 1,
    "none": 0,
}

#: A review in one of these states is closed: it leaves every open list.
CLOSED_STATUSES: frozenset[str] = frozenset({"signed_off", "superseded"})

#: Fields whose change means the clinical content of a call changed.
CLINICAL_FIELDS: tuple[str, ...] = ("follow_up", "outcome", "survey_responses")

#: Marker the nurse assistant sends as X-Acting-User when it could not verify who
#: is signed in. Never a signer.
UNVERIFIED_MARKERS: tuple[str, ...] = ("not verified", "unverified")


# ---------------------------------------------------------------------------
# Small predicates
# ---------------------------------------------------------------------------


def is_assessment(doc: Mapping[str, Any]) -> bool:
    """A raised flag, a nurse's tier change, or a completed questionnaire.

    Same rule as the dashboard's ``isAssessment``. A voicemail filed with
    follow_up "none" is not one: it neither opens nor closes anything.
    """
    return (
        doc.get("follow_up") in NEEDS_TIERS
        or bool(doc.get("follow_up_override"))
        or (bool(doc.get("follow_up")) and doc.get("outcome") == "questionnaire_completed")
    )


def review_closed(doc: Mapping[str, Any]) -> bool:
    return (doc.get("review") or {}).get("status") in CLOSED_STATUSES


def has_export(review: Mapping[str, Any] | None) -> bool:
    """Does this closed review have a record in the EHR outbox? A sign-off does;
    so does an earlier report RESOLVED together with a later one (D-1). A report
    merely superseded does not."""
    review = review or {}
    return review.get("status") == "signed_off" or (
        review.get("status") == "superseded" and bool(review.get("resolved"))
    )


def is_open_follow_up(doc: Mapping[str, Any]) -> bool:
    """Under D-1 "open" is simply: the tier needs a callback and the review is not
    closed, whatever came after it."""
    return doc.get("follow_up") in NEEDS_TIERS and not review_closed(doc)


def sort_open_follow_ups(rows: Iterable[Doc]) -> list[Doc]:
    """Most severe first, then oldest first."""
    order = {t: i for i, t in enumerate(NEEDS_TIERS)}
    return sorted(
        rows, key=lambda c: (order.get(c.get("follow_up"), 9), c.get("initiated_at") or "")
    )


def patient_filter(doc: Mapping[str, Any]) -> tuple[str, str] | None:
    """How to find a patient's other calls: by MRN when the call has one, else by
    phone (and then only calls with no MRN either; see ``same_patient``)."""
    if doc.get("patient_id"):
        return "patient_id", str(doc["patient_id"])
    if doc.get("to"):
        return "to", str(doc["to"])
    return None


def same_patient(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """MRN when either side has one; phone only when neither does (B-6)."""
    if a.get("patient_id") or b.get("patient_id"):
        return bool(a.get("patient_id")) and a.get("patient_id") == b.get("patient_id")
    return bool(a.get("to")) and a.get("to") == b.get("to")


def verified_signer(raw: str | None) -> str | None:
    """The signer's identity, or None when it is missing or unverified (B-7)."""
    s = " ".join((raw or "").split())
    if not s or s == "dashboard" or any(m in s.lower() for m in UNVERIFIED_MARKERS):
        return None
    return s[:200]


def clean_note(note: str | None) -> str | None:
    return " ".join((note or "").split()) or None


# ---------------------------------------------------------------------------
# EHR outbox payload
# ---------------------------------------------------------------------------


def ehr_payload(call_id: str, doc: Mapping[str, Any], review: Mapping[str, Any]) -> Doc:
    """The structured result, FHIR-shaped: a QuestionnaireResponse for the answers,
    the disposition, and a Task when the follow-up was resolved. Never the DOB."""
    answers = {
        k: v for k, v in (doc.get("survey_responses") or {}).items() if not str(k).startswith("_")
    }
    out: Doc = {
        "call_sid": call_id,
        "patient": {"mrn": doc.get("patient_id"), "name": doc.get("patient_name")},
        "encounter": {
            "date": doc.get("ended_at") or doc.get("initiated_at"),
            "channel": "sms" if doc.get("channel") == "sms" else "phone",
            "identity_check": doc.get("dob_verification_result"),
        },
        "questionnaire_response": {
            "resourceType": "QuestionnaireResponse",
            "status": "completed",
            "item": [{"linkId": k, "answer": v} for k, v in answers.items()],
        },
        "disposition": {
            "follow_up": doc.get("follow_up"),
            "reason": doc.get("follow_up_reason"),
            "agent_follow_up": doc.get("agent_follow_up", doc.get("follow_up")),
            "changed_by_nurse": doc.get("follow_up_override"),
        },
        "review": dict(review),
    }
    if review.get("resolved"):
        out["task"] = {
            "resourceType": "Task",
            "status": "completed",
            "description": "Post-op RN follow-up",
            "note": review.get("note"),
            "owner": review.get("by"),
            "lastModified": review.get("at"),
        }
    return out


def withdrawn_export(now: str, because: str | None = None) -> Doc:
    out: Doc = {"status": "withdrawn", "withdrawn_at": now}
    if because:
        out["withdrawn_because"] = because
    return out


# ---------------------------------------------------------------------------
# Reopening
# ---------------------------------------------------------------------------


def reopen_after_tier_change(existing: Mapping[str, Any], *, now: str, by: str) -> tuple[Doc, bool]:
    """(extra call-doc fields, withdraw the export?) when a nurse changes the tier.

    A changed tier invalidates a sign-off made on the old one. A superseded report
    is closed too: a nurse re-tiering it (say, escalating an old report she has just
    listened to) must bring it back into the lists, not leave it hidden (B-5).
    """
    review = existing.get("review") or {}
    if review.get("status") not in CLOSED_STATUSES:
        return {}, False
    reopened = {
        **review,
        "status": "reopened",
        "reopened_at": now,
        "reopened_by": by,
        "reopened_because": "follow-up changed",
    }
    patch = {
        "review": reopened,
        "review_history": [*(existing.get("review_history") or []), reopened],
    }
    return patch, has_export(review)


def merge_late_report(
    existing: Mapping[str, Any] | None, patch: Mapping[str, Any], *, now: str
) -> tuple[Doc, bool]:
    """(patch to write, withdraw the export?) for a report arriving on a call a
    nurse may already have handled (B-5).

    The by-text questionnaire files onto the ORIGINAL call and its session lives
    for days, so a patient can finish it after a nurse changed the tier or signed
    the call off.

    * A tier a nurse holds (she changed it, or signed the call off) stands unless
      the report is MORE severe; then the report escalates it and the nurse's
      override no longer describes the tier, so it is cleared. Either way the
      report is recorded as ``agent_follow_up`` and in ``follow_up_history``.
    * A closed review is reopened when the clinical content changed; its pending
      EHR export (if it has one) must be withdrawn.
    """
    existing = existing or {}
    p: Doc = dict(patch)
    new_tier = p.get("follow_up")
    review = existing.get("review") or {}
    # A tier raised by the post-call translation re-check is held the same way:
    # a later, milder report must never lower it (docs/LANGUAGES-PLAN.md §3.3).
    held = (
        bool(existing.get("follow_up_override"))
        or review.get("status") in CLOSED_STATUSES
        or any(
            (existing.get(k) or {}).get("held") or (existing.get(k) or {}).get("raised_to")
            for k in ("translation_check", "text_translation_check")
        )
    )
    if new_tier and held:
        current = existing.get("follow_up")
        escalates = TIER_RANK.get(new_tier, 0) > TIER_RANK.get(current, 0)
        p["agent_follow_up"] = new_tier
        if new_tier != current:
            p["follow_up_history"] = [
                *(existing.get("follow_up_history") or []),
                {
                    "at": now,
                    "from": current,
                    "to": new_tier if escalates else current,
                    "by": "report",
                    "reason": (
                        "A later report escalated it."
                        if escalates
                        else f"A later report said {new_tier}; the nurse's tier was kept."
                    ),
                },
            ]
        if escalates:
            p["follow_up_override"] = None
        else:
            p.pop("follow_up", None)
            p.pop("follow_up_reason", None)
    changed = any(k in p and p[k] != existing.get(k) for k in CLINICAL_FIELDS)
    withdraw = False
    if review.get("status") in CLOSED_STATUSES and changed:
        reopened = {
            **review,
            "status": "reopened",
            "reopened_at": now,
            "reopened_by": "report",
            "reopened_because": "a later report arrived for this call",
        }
        if has_export(review):
            reopened["export_withdrawn"] = True
            withdraw = True
        p["review"] = reopened
        p["review_history"] = [*(existing.get("review_history") or []), reopened]
    return p, withdraw


# ---------------------------------------------------------------------------
# Sign-off
# ---------------------------------------------------------------------------


@dataclass
class SignOffPlan:
    """What a sign-off would do. ``status`` is ``signed_off`` or the refusal."""

    status: str
    review: Doc | None = None
    #: The newer open report that replaces this one (superseded_by_later).
    latest: str | None = None
    #: The tier now on the call (changed).
    follow_up: str | None = None
    #: Earlier open reports that needed a callback: resolved by this sign-off.
    earlier_needs: list[str] = field(default_factory=list)
    #: Earlier open reports closed by this sign-off: call id -> closed review.
    closes: dict[str, Doc] = field(default_factory=dict)
    #: EHR outbox records to write: call id -> record.
    exports: dict[str, Doc] = field(default_factory=dict)

    @property
    def superseded(self) -> list[str]:
        return list(self.closes)


def plan_sign_off(
    call_id: str,
    doc: Mapping[str, Any] | None,
    others: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    signer: str,
    now: str,
    note: str | None = None,
    expected_follow_up: str | None = None,
    bulk: bool = False,
) -> SignOffPlan:
    """Sign off one call.

    Only the patient's LATEST open assessment can be signed off (a later open
    report replaces an earlier one as the patient's current state; a later report
    already closed does not, so a reopened earlier report can still be resolved).
    Signing it off closes every earlier open report for the patient (D-1):

    * an earlier report that needed a callback is not closed silently: the
      sign-off becomes a resolve, needs the nurse's note, closes each such report
      with that note (``resolved``) and exports it too;
    * an earlier report that needed nothing is closed as superseded and not
      exported (the current report covers it).

    ``others`` may hold any calls; only the same patient's assessments count.
    ``bulk`` refuses anything that resolves a follow-up (needs_individual_review).
    """
    if doc is None:
        return SignOffPlan("not_found")
    if not is_assessment(doc):
        return SignOffPlan("not_an_assessment")
    if review_closed(doc):
        return SignOffPlan("already_signed_off")
    mine = doc.get("initiated_at") or ""
    peers = [
        (sid, d)
        for sid, d in others
        if sid != call_id and is_assessment(d) and same_patient(doc, d)
    ]
    later = [
        (sid, d)
        for sid, d in peers
        if (d.get("initiated_at") or "") > mine and not review_closed(d)
    ]
    if later:
        latest = max(later, key=lambda x: x[1].get("initiated_at") or "")[0]
        return SignOffPlan("superseded_by_later", latest=latest)
    tier = doc.get("follow_up")
    if expected_follow_up is not None and expected_follow_up != tier:
        return SignOffPlan("changed", follow_up=tier)
    earlier = [
        (sid, d)
        for sid, d in peers
        if (d.get("initiated_at") or "") < mine and not review_closed(d)
    ]
    earlier_needs = [sid for sid, d in earlier if d.get("follow_up") in NEEDS_TIERS]
    resolving = tier in NEEDS_TIERS or bool(earlier_needs)
    if resolving and bulk:
        return SignOffPlan("needs_individual_review", earlier_needs=earlier_needs)
    note = clean_note(note)
    if resolving and len(note or "") < 5:
        return SignOffPlan("note_required", earlier_needs=earlier_needs)
    review: Doc = {
        "status": "signed_off",
        "by": signer,
        "at": now,
        "note": note,
        "follow_up": tier,
        "resolved": resolving,
    }
    if earlier_needs:
        review["also_resolved"] = earlier_needs
    plan = SignOffPlan("signed_off", review=review, earlier_needs=earlier_needs)
    plan.exports[call_id] = {
        **ehr_payload(call_id, doc, review),
        "status": "pending_export",
        "created_at": now,
    }
    for sid, edoc in earlier:
        needed = sid in earlier_needs
        closed = {
            "status": "superseded",
            "by": signer,
            "at": now,
            "superseded_by": call_id,
            "follow_up": edoc.get("follow_up"),
            "resolved": needed,
            "note": (
                f"Resolved with the later report {call_id}: {note}"
                if needed
                else f"Superseded by the later report {call_id}, signed off by {signer}."
            ),
        }
        plan.closes[sid] = closed
        if needed:
            plan.exports[sid] = {
                **ehr_payload(sid, edoc, closed),
                "status": "pending_export",
                "created_at": now,
                "closed_with": call_id,
            }
    return plan


def close_patch(doc: Mapping[str, Any], review: Mapping[str, Any], now: str) -> Doc:
    """Call-doc fields recording ``review`` (sign-off or superseded close)."""
    return {
        "review": dict(review),
        "review_history": [*(doc.get("review_history") or []), dict(review)],
        "updated_at": now,
    }
