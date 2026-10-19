"""Call-lifecycle rules: status merging, completed-call classification and AMD.

Pure functions only. Nothing here reads Firestore, the clock, the environment or a
telephony SDK: the repository layer reads the current call doc inside a transaction,
calls these functions with an explicit ``now``, and writes what they return.

The rules were hardened over many incidents in the previous proxy; the comments
record *why* each one exists, because every one of them looks removable.

Call ids are provider-neutral strings. Nothing in this module knows what a provider's
call id looks like.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from voa_core import review

Doc = dict[str, Any]

# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

SUCCESS = "success"
LEFT_VMAIL = "left_vmail"
RNA = "rna"
BUSY = "busy"
INTERRUPTED = "interrupted"
FAILED = "failed"
CANCELED = "canceled"

#: Once a call doc carries one of these, ordinary updates may not change it.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {SUCCESS, LEFT_VMAIL, RNA, BUSY, INTERRUPTED, FAILED, CANCELED}
)

#: Terminal statuses a post-call report may still correct to success/left_vmail.
#: ``left_vmail`` is here because AMD can write it with no report: a false machine
#: verdict must never outrank a questionnaire the patient actually completed.
#: ``busy`` is here because a busy call can trigger the follow-up text, and so a
#: completed by-text questionnaire.
REPORT_PROMOTABLE: frozenset[str] = frozenset({INTERRUPTED, RNA, LEFT_VMAIL, BUSY})

#: The statuses a report promotion may write.
REPORT_PROMOTION_TARGETS: frozenset[str] = frozenset({SUCCESS, LEFT_VMAIL})

#: Report outcomes that do NOT mean the patient was reached. The report still
#: attaches to the call, but it must not promote the call to success or clear the
#: call-list entry: an identity failure promoted to success closes the REAL
#: patient's follow-up and a nurse rings the same wrong person.
NON_REACHING_OUTCOMES: frozenset[str] = frozenset(
    {"unable_to_reach_wrong_person", "identity_verification_failed", "consent_declined"}
)

#: Timeline fields that are set once. Duplicate or late provider callbacks are no-ops.
SET_IF_ABSENT_FIELDS: tuple[str, ...] = ("ringing_at", "answered_at", "ended_at")

#: Patient-context keys that must never be written to a call doc (review C-2: the
#: call doc is served to the dashboard; the DOB lives only in the read-once
#: pending-call record and in the agent's grounding note).
CALL_DOC_PHI_FIELDS: frozenset[str] = frozenset({"patient_dob"})


class CallEventKind(StrEnum):
    """Provider-neutral call progress events. Provider adapters map onto these."""

    QUEUED = "queued"  # created / initiated: nothing to record but the raw status
    RINGING = "ringing"
    ANSWERED = "answered"  # Twilio's "in-progress"
    COMPLETED = "completed"  # ended after being answered (or not): needs classifying
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"  # an event we don't recognise: record the raw status only


#: Events that settle the call status directly, without classification.
EVENT_TERMINAL_STATUS: Mapping[CallEventKind, str] = {
    CallEventKind.BUSY: BUSY,
    CallEventKind.NO_ANSWER: RNA,
    CallEventKind.FAILED: FAILED,
    CallEventKind.CANCELED: CANCELED,
}


def iso(now: datetime) -> str:
    """UTC ISO-8601 for a timezone-aware ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# PHI
# ---------------------------------------------------------------------------


def strip_phi_for_call_doc(patient_context: Mapping[str, Any] | None) -> Doc:
    """A copy of ``patient_context`` that is safe to store on a call doc (no DOB)."""
    return {k: v for k, v in (patient_context or {}).items() if k not in CALL_DOC_PHI_FIELDS}


def _strip_phi_from_doc(doc: Doc) -> Doc:
    out = {k: v for k, v in doc.items() if k not in CALL_DOC_PHI_FIELDS}
    ctx = out.get("patient_context")
    if isinstance(ctx, Mapping):
        out["patient_context"] = strip_phi_for_call_doc(ctx)
    return out


# ---------------------------------------------------------------------------
# The transactional merge (replaces _update_call_doc's read-decide-write)
# ---------------------------------------------------------------------------


def effective_call_patch(
    current: Mapping[str, Any] | None, patch: Mapping[str, Any], *, from_report: bool = False
) -> Doc:
    """The fields of ``patch`` that may actually be written over ``current``.

    * A terminal ``call_status`` is never overwritten, EXCEPT that a report may
      promote a ``REPORT_PROMOTABLE`` status to success/left_vmail. A disallowed
      ``call_status`` is dropped but the rest of the patch is KEPT: telemetry from
      a real terminal event (ended_at, duration) must still land when a report beat
      the completed callback to the doc.
    * ``ringing_at``/``answered_at``/``ended_at`` are set-if-absent.
    * DOB fields are always dropped (C-2).
    """
    existing = current or {}
    p = _strip_phi_from_doc(dict(patch))
    cur_status = existing.get("call_status")
    if cur_status in TERMINAL_STATUSES:
        new_status = p.get("call_status")
        promotion = (
            from_report
            and cur_status in REPORT_PROMOTABLE
            and new_status in REPORT_PROMOTION_TARGETS
        )
        if not promotion:
            p.pop("call_status", None)
    for field in SET_IF_ABSENT_FIELDS:
        if field in p and existing.get(field):
            p.pop(field)
    return p


def merge_call_doc(
    current: Mapping[str, Any] | None,
    patch: Mapping[str, Any],
    *,
    now: datetime,
    from_report: bool = False,
) -> Doc | None:
    """The new call doc after applying ``patch``, or None when nothing changes.

    None means "do not write" (the ``DocStore.transact`` mutator contract), which is
    what happens when every field of the patch was disallowed: e.g. a stale
    ``interrupted`` arriving on a ``left_vmail`` doc. ``updated_at`` is stamped only
    when something is written.
    """
    p = effective_call_patch(current, patch, from_report=from_report)
    if not p:
        return None
    if from_report:
        # A report on a call a nurse already handled (B-5): her tier stands
        # against a milder report, and a closed review is reopened.
        p, _withdraw = review.merge_late_report(current, p, now=iso(now))
    p["updated_at"] = iso(now)
    return _strip_phi_from_doc({**(current or {}), **p})


def stale_status_dropped(
    current: Mapping[str, Any] | None, patch: Mapping[str, Any], *, from_report: bool = False
) -> bool:
    """True when ``patch`` carried a different ``call_status`` that the merge drops.
    For logging only."""
    new = patch.get("call_status")
    if new is None or new == (current or {}).get("call_status"):
        return False
    return "call_status" not in effective_call_patch(current, patch, from_report=from_report)


# ---------------------------------------------------------------------------
# Provider events -> patch (replaces the body of handle_call_status)
# ---------------------------------------------------------------------------


def call_event_patch(
    kind: CallEventKind | str,
    *,
    now: datetime,
    current: Mapping[str, Any] | None = None,
    provider_status: str | None = None,
    answered_by: str | None = None,
    duration_seconds: int | str | None = None,
    failure_code: str | int | None = None,
    failure_message: str | None = None,
) -> Doc:
    """The patch a provider status event produces. Feed it to ``merge_call_doc``.

    ``current`` is only consulted for ``COMPLETED``, which is classified from the
    doc plus this event's own ``answered_by`` (the verdict can arrive in the same
    request as the completed event, before it is on the doc).
    """
    kind = CallEventKind(kind)
    now_s = iso(now)
    patch: Doc = {"last_provider_status": provider_status or kind.value}
    verdict = (answered_by or "").strip()
    if verdict:
        patch["answered_by"] = verdict
        # Stamped here too: without it a verdict delivered alongside "completed"
        # has no timing and AMD latency measurements have a hole.
        patch["amd_at"] = now_s
    if kind is CallEventKind.RINGING:
        patch["ringing_at"] = now_s
    elif kind is CallEventKind.ANSWERED:
        patch["answered_at"] = now_s
    elif kind is CallEventKind.COMPLETED:
        patch["ended_at"] = now_s
        if duration_seconds is not None and str(duration_seconds).isdigit():
            patch["duration_seconds"] = int(duration_seconds)
        patch["call_status"] = classify_completed(current, verdict or None)
    elif kind in EVENT_TERMINAL_STATUS:
        patch["ended_at"] = now_s
        patch["call_status"] = EVENT_TERMINAL_STATUS[kind]
        if kind is CallEventKind.FAILED:
            if failure_code:
                patch["failure_code"] = str(failure_code)
            if failure_message:
                patch["failure_message"] = failure_message
    return patch


# ---------------------------------------------------------------------------
# AMD
# ---------------------------------------------------------------------------


class AmdVerdict(StrEnum):
    HUMAN = "human"
    MACHINE_BEEP = "machine_beep"  # greeting finished, mailbox is recording
    # Also what a human who talks past the speech threshold and pauses looks like.
    MACHINE_AMBIGUOUS = "machine_ambiguous"
    MACHINE_START = "machine_start"  # a machine, detected at pickup
    FAX = "fax"
    UNKNOWN = "unknown"


class VerdictAction(StrEnum):
    HOLD = "hold"  # no verdict yet: keep waiting
    RELEASE = "release"  # bridge the agent to the callee (always safe)
    DROP = "drop"  # play the canned voicemail clip, then hang up
    HANGUP = "hangup"  # fax: hang up and leave nothing


_PROVIDER_VERDICTS: Mapping[str, AmdVerdict] = {
    "human": AmdVerdict.HUMAN,
    "machine_end_beep": AmdVerdict.MACHINE_BEEP,
    "machine_end_silence": AmdVerdict.MACHINE_AMBIGUOUS,
    "machine_end_other": AmdVerdict.MACHINE_AMBIGUOUS,
    "machine_start": AmdVerdict.MACHINE_START,
    "fax": AmdVerdict.FAX,
    "unknown": AmdVerdict.UNKNOWN,
}

MACHINE_VERDICTS: frozenset[AmdVerdict] = frozenset(
    {
        AmdVerdict.MACHINE_BEEP,
        AmdVerdict.MACHINE_AMBIGUOUS,
        AmdVerdict.MACHINE_START,
        AmdVerdict.FAX,
    }
)

#: The only verdict that takes the canned-clip path by default: unambiguous.
DEFAULT_VM_DROP_VERDICTS: frozenset[AmdVerdict] = frozenset({AmdVerdict.MACHINE_BEEP})


def normalize_amd_verdict(raw: str | AmdVerdict | None) -> AmdVerdict | None:
    """Map a provider (or already-normalised) verdict string to ``AmdVerdict``.

    None/blank means "no verdict yet". Any other unrecognised ``machine*`` string
    is a definite machine (``MACHINE_START``), matching the old proxy, which
    classified every ``machine*`` verdict as a machine but only flagged the two
    ambiguous ones. Anything else unrecognised is ``UNKNOWN``: junk must never be
    read as a machine.
    """
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if not v:
        return None
    if v in _PROVIDER_VERDICTS:
        return _PROVIDER_VERDICTS[v]
    try:
        return AmdVerdict(v)
    except ValueError:
        pass
    return AmdVerdict.MACHINE_START if v.startswith("machine") else AmdVerdict.UNKNOWN


def is_machine(verdict: str | AmdVerdict | None) -> bool:
    return normalize_amd_verdict(verdict) in MACHINE_VERDICTS


def verdict_action(
    verdict: str | AmdVerdict | None,
    vm_drop_enabled: bool = True,
    vm_drop_verdicts: Iterable[str | AmdVerdict] = DEFAULT_VM_DROP_VERDICTS,
) -> VerdictAction:
    """What the live media session should do with a verdict.

    Fax always hangs up silently, kill switch or not. A verdict in
    ``vm_drop_verdicts`` drops only while ``vm_drop_enabled``. Human, unknown and
    any machine verdict we have chosen not to act on release: exactly the
    behaviour without AMD.
    """
    v = normalize_amd_verdict(verdict)
    if v is None:
        return VerdictAction.HOLD
    if v is AmdVerdict.FAX:
        return VerdictAction.HANGUP
    drop_set = {normalize_amd_verdict(x) for x in vm_drop_verdicts}
    if vm_drop_enabled and v in drop_set:
        return VerdictAction.DROP
    return VerdictAction.RELEASE


def release_marks_amd_released(verdict: str | AmdVerdict | None) -> bool:
    """Should releasing on this verdict set ``amd_released`` on the call doc?

    ONLY the ambiguous machine verdicts: a real conversation can follow them, and
    classifying that call as left_vmail would text a patient we actually spoke to.
    Emphatically not ``machine_start``/``machine_beep``: those are real mailboxes,
    and flagging them would suppress the left_vmail the follow-up text depends on.
    """
    return normalize_amd_verdict(verdict) is AmdVerdict.MACHINE_AMBIGUOUS


def late_verdict_promotes(current: Mapping[str, Any] | None, verdict: str | AmdVerdict) -> bool:
    """Does a machine verdict arriving after the call ended relabel it left_vmail?

    Only ``interrupted`` is promoted, and only when no report outcome exists and the
    live session did not deliberately release on the verdict.
    """
    doc = current or {}
    return (
        is_machine(verdict)
        and doc.get("call_status") == INTERRUPTED
        and not doc.get("outcome")
        and not doc.get("amd_released")
    )


def apply_amd_verdict(
    current: Mapping[str, Any] | None, answered_by: str, *, now: datetime
) -> tuple[Doc, bool]:
    """(new doc, promoted) for an async AMD callback (replaces _record_amd_result).

    Always records the verdict and ``amd_at``; promotes interrupted -> left_vmail
    per ``late_verdict_promotes``. Must run inside the same transaction as the read:
    a separate read leaves a window where the completed callback classifies without
    the verdict and this declines to promote, stranding the call as interrupted.
    When ``promoted``, the caller settles the call-list entry as left_vmail.
    """
    now_s = iso(now)
    patch: Doc = {"answered_by": answered_by, "amd_at": now_s, "updated_at": now_s}
    promoted = late_verdict_promotes(current, answered_by)
    if promoted:
        patch["call_status"] = LEFT_VMAIL
    return _strip_phi_from_doc({**(current or {}), **patch}), promoted


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_completed(doc: Mapping[str, Any] | None, answered_by_hint: str | None = None) -> str:
    """Disposition for a call that reached "completed" (replaces _classify_completed).

    Order matters:
      1. A report outcome wins: voicemail_left -> left_vmail, anything else -> success,
         EXCEPT outcomes that did not reach the patient (wrong person, identity
         failed, consent declined): those fall through to the inference below, so
         the patient's follow-up stays open. (The report path already withheld
         promotion for them; this closes the same hole when Twilio's "completed"
         arrives after the report. Found on the first real call.)
      2. A machine verdict -> left_vmail, unless the live session released on it
         (``amd_released``): then the agent went on talking to whoever it was.
         ``answered_by_hint`` (this event's verdict) beats the stored one.
      3. answered_at -> interrupted (a person hung up mid-call), else rna.
    """
    d = doc or {}
    outcome = d.get("outcome")
    if outcome == "voicemail_left":
        return LEFT_VMAIL
    if outcome and outcome not in NON_REACHING_OUTCOMES:
        return SUCCESS
    answered_by = (answered_by_hint or d.get("answered_by") or "").strip()
    if not d.get("amd_released") and is_machine(answered_by):
        return LEFT_VMAIL
    if d.get("answered_at"):
        return INTERRUPTED
    return RNA


def call_leg_still_live(doc: Mapping[str, Any] | None, *, read_failed: bool = False) -> bool:
    """Is the call this doc describes still in progress?

    Used to decide whether a stranded "calling" call-list entry may be redialled.
    An unreadable doc counts as live so a datastore blip can never ring a patient
    twice; a missing doc counts as dead.
    """
    if read_failed:
        return True
    if doc is None:
        return False
    return doc.get("call_status") not in TERMINAL_STATUSES
