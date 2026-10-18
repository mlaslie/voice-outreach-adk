"""The model's tools, as ADK FunctionTools.

The model never writes A2UI. Each tool calls a view or a ``propose_*`` action,
which returns ``(card, data)``: the card is appended to the current turn (and
reaches the user as an A2UI surface, or text in text mode), and only the compact
``data`` goes back to the model.

``propose_*`` tools never act: they store a proposal and show a confirmation
card. Deliberately absent: deleting call records and resetting the worklist
(neither is a nurse's task in chat), and report sign-off (the API has none).
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Literal

from google.adk.tools import FunctionTool

from . import actions, turn
from . import worklists as wl
from .backend import BackendError
from .cards import error_card

log = logging.getLogger(__name__)

Tier = Literal["emergency", "urgent_same_day", "routine_next_business_day", "informational", "none"]
CallStatus = Literal[
    "all", "active", "success", "left_vmail", "rna", "busy", "failed", "interrupted", "canceled"
]


def card_tool(fn):
    """Run a (card, data) action, put the card on the turn, return the data."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        t = turn.current()
        t0 = time.monotonic()
        try:
            card, data = await fn(*args, **kwargs)
        except BackendError as e:
            card, data = error_card(str(e)), {"error": str(e)}
        except (TypeError, KeyError, ValueError) as e:
            card, data = None, {"error": f"bad arguments: {type(e).__name__}"}
        if card is not None:
            t.cards.append(card)
        t.trace.append(
            {
                "name": fn.__name__,
                "args": sorted(kwargs),
                "ms": round((time.monotonic() - t0) * 1000),
                "card": card is not None,
            }
        )
        return {"result": data}

    return wrapper


# --- the nurse's five questions (prefer these over list_calls) ----------------------------


@card_tool
async def to_be_called():
    """Who is going to be called, and when: patients in upcoming call runs, anyone on the
    phone now, and callbacks patients booked by text."""
    return await wl.view_queue()


@card_tool
async def called_today():
    """Who has been called today and what happened, grouped by what the nurse must do:
    needs you now, next business day, cleared, not reached (voicemail/no answer, text
    sent, callback booked), asked to be called another time, cancelled."""
    return await wl.view_called()


@card_tool
async def needs_follow_up(group: Literal["now", "next_business_day", "all"] = "now"):
    """Patients who need an RN callback, with the reason and when it is due.

    Args:
        group: 'now' = emergent and same-day; 'next_business_day' = routine; 'all' = both.
    """
    if group == "all":
        return await actions.view_follow_up_queue()
    return await wl.view_needs("next_day" if group == "next_business_day" else "now")


@card_tool
async def cleared_patients():
    """Patients assessed today who need no follow-up, each with why (what they reported)."""
    return await wl.view_cleared()


@card_tool
async def propose_change_follow_up(
    patient: str | None = None,
    call_sid: str | None = None,
    tier: Tier | None = None,
    reason: str | None = None,
):
    """Change a patient's follow-up level (this is also how a follow-up is closed). A
    reason is required and must come from the user; if they gave none, omit it and a
    form asks for it. Shows a confirmation card.

    Args:
        patient: Patient name, MRN or phone; their latest call is used.
        call_sid: A specific call id, when known.
        tier: The new follow-up level.
        reason: The user's own reason for the change. Never invent one.
    """
    c, choices = await actions.resolve(call_sid, patient)
    if choices:
        return actions.which_patient(patient or "", choices, "wl_tier_form")
    if not c:
        return None, {"error": f"no call found for {patient or call_sid}"}
    if not tier or not (reason or "").strip():
        return await wl.tier_form(c["doc_id"], {"tier": tier or "", "reason": reason or ""})
    return await wl.propose_tier(c["doc_id"], tier, reason)


@card_tool
async def propose_cancel_call(patient: str, reason: str | None = None):
    """Cancel a patient's upcoming scheduled call. A reason is required and must come
    from the user; if they gave none, omit it and a form asks for it. Shows a
    confirmation card.

    Args:
        patient: Patient name or MRN.
        reason: The user's own reason. Never invent one.
    """
    found = await wl.entries_matching(patient, "pending")
    if not found:
        return None, {"error": f"{patient} has no upcoming call to cancel"}
    if len(found) > 1:
        return actions.which_patient(patient, found, "wl_cancel_form", key="entry_id")
    entry_id = found[0]["doc_id"]
    if not (reason or "").strip():
        return await wl.cancel_form(entry_id)
    return await wl.propose_cancel(entry_id, reason)


@card_tool
async def reschedule_call(patient: str):
    """Put a cancelled patient back on the call queue. Shows a form with the upcoming
    runs, the next opening and a time picker, limited to calling hours; the nurse picks
    and confirms there. Never choose the time yourself.

    Args:
        patient: Patient name or MRN.
    """
    found = await wl.entries_matching(patient, "canceled")
    if not found:
        return None, {"error": f"{patient} has no cancelled call to reschedule"}
    if len(found) > 1:
        return actions.which_patient(patient, found, "wl_reschedule_form", key="entry_id")
    return await wl.reschedule_form(found[0]["doc_id"])


@card_tool
async def patient_history(patient: str | None = None, call_sid: str | None = None):
    """Everything about one patient, newest first: calls, texts, cancellations,
    follow-up changes, and what is planned next.

    Args:
        patient: Patient name, MRN or phone.
        call_sid: A specific call id, when known.
    """
    return await wl.view_history(call_sid=call_sid, patient=patient)


# --- calls ------------------------------------------------------------------------------


@card_tool
async def list_calls(
    status: CallStatus = "all",
    needs_follow_up: bool = False,
    patient: str | None = None,
    today: bool = False,
    before_today: bool = False,
    since_hours: int | None = None,
):
    """List follow-up calls, newest first, with optional filters.

    Args:
        status: Call outcome filter.
        needs_follow_up: Only patients whose latest call needs a callback.
        patient: Patient name, MRN or phone.
        today: Only calls placed today (clinic time).
        before_today: Only past calls, placed before today (clinic time).
        since_hours: Only calls placed in the last N hours.
    """
    return await actions.view_calls(
        status=status,
        needs_follow_up=needs_follow_up,
        patient=patient,
        today=today,
        before_today=before_today,
        since_hours=since_hours,
    )


@card_tool
async def follow_up_queue():
    """Patients who need RN follow-up, highest acuity first. Use for 'who needs a
    callback', 'any urgent patients', 'what needs attention'."""
    return await actions.view_follow_up_queue()


@card_tool
async def get_call(call_sid: str | None = None, patient: str | None = None):
    """One call in detail: disposition, symptom review, timing.

    Args:
        call_sid: A specific call id, when known.
        patient: Patient name, MRN or phone; the latest call is used.
    """
    return await actions.view_call(call_sid=call_sid, patient=patient)


@card_tool
async def get_transcript(call_sid: str | None = None, patient: str | None = None):
    """The transcript of one call between the automated caller and the patient (or the
    text messages, for a questionnaire completed by text).

    Args:
        call_sid: A specific call id, when known.
        patient: Patient name, MRN or phone; the latest call is used.
    """
    return await actions.view_transcript(call_sid=call_sid, patient=patient)


# --- worklist, texts, allowlist, help ------------------------------------------------------


@card_tool
async def show_call_list():
    """The outreach worklist (patients pending a batch of calls) and outreach progress."""
    return await actions.view_call_list()


@card_tool
async def show_sms_followups():
    """Text message follow-up threads, scheduled callbacks and the 24-hour re-text limit."""
    return await actions.view_sms_followups()


@card_tool
async def show_allowlist():
    """Phone numbers approved for calls (pilot safeguard)."""
    return await actions.view_allowlist()


@card_tool
async def show_help():
    """What this assistant can do (the home card)."""
    return await wl.view_home()


# --- acting (always through a confirmation card) -----------------------------------------


@card_tool
async def propose_place_call(
    phone: str,
    patient_name: str,
    patient_id: str,
    patient_dob: str,
    operation_name: str | None = None,
    operation_date: str | None = None,
    is_diabetic: bool = False,
):
    """Prepare a follow-up call to one patient now. Shows a confirmation card; nothing
    is dialled until the user confirms.

    Args:
        phone: E.164, e.g. +12025550123.
        patient_name: First and last name.
        patient_id: The MRN.
        patient_dob: YYYY-MM-DD.
        operation_name: The procedure, if known.
        operation_date: YYYY-MM-DD, if known.
        is_diabetic: Adds the blood glucose questions.
    """
    card, data = await actions.propose_place_call(
        phone=phone,
        patient_name=patient_name,
        patient_id=patient_id,
        patient_dob=patient_dob,
        operation_name=operation_name,
        operation_date=operation_date,
        is_diabetic=is_diabetic,
    )
    return card, data


@card_tool
async def show_place_call_form(
    phone: str | None = None,
    patient_name: str | None = None,
    patient_id: str | None = None,
    patient_dob: str | None = None,
    operation_name: str | None = None,
    operation_date: str | None = None,
    is_diabetic: bool = False,
):
    """Show a form for calling a patient, pre-filled with any known details. The user
    fills it in and presses Review, which leads to a confirmation card.

    Args:
        phone: E.164, if known.
        patient_name: If known.
        patient_id: The MRN, if known.
        patient_dob: YYYY-MM-DD, if known.
        operation_name: If known.
        operation_date: YYYY-MM-DD, if known.
        is_diabetic: If known.
    """
    if not turn.current().renders_cards:
        return None, {
            "status": "text_mode",
            "note": "Forms are unavailable; ask for the missing fields.",
        }
    prefill = {
        "phone": actions.normalize_phone(phone) if phone else "",
        "patient_name": patient_name,
        "patient_id": patient_id,
        "patient_dob": patient_dob,
        "operation_name": operation_name,
        "operation_date": operation_date,
        "is_diabetic": is_diabetic,
    }
    return await actions.show_form(prefill)


@card_tool
async def propose_redial(call_sid: str | None = None, patient: str | None = None):
    """Prepare to call a patient back, reusing the details from their earlier call.

    Args:
        call_sid: A specific call id, when known.
        patient: Patient name, MRN or phone; the latest call is used.
    """
    c, choices = await actions.resolve(call_sid, patient)
    if choices:
        return actions.which_patient(patient or "", choices, "propose_redial")
    if not c:
        return None, {"error": f"no call found for {patient or call_sid}"}
    return await actions.propose_redial(c["doc_id"])


@card_tool
async def propose_add_to_call_list(entries: list[dict]):
    """Prepare to add patients to the outreach worklist (not called until outreach
    starts). Shows a confirmation card.

    Args:
        entries: One object per patient with phone (E.164), patient_name, patient_id
            (MRN), patient_dob (YYYY-MM-DD), and optionally operation_name,
            operation_date (YYYY-MM-DD) and is_diabetic (boolean).
    """
    return await actions.propose_add_entries(entries)


@card_tool
async def propose_remove_from_call_list(patient_id: str):
    """Prepare to remove one patient (by MRN) from the outreach worklist.

    Args:
        patient_id: The MRN.
    """
    return await actions.propose_remove_entry(patient_id=patient_id)


@card_tool
async def propose_allow_number(phone: str, label: str = ""):
    """Prepare to approve a phone number for calls.

    Args:
        phone: E.164.
        label: A short note, e.g. whose number it is.
    """
    return await actions.propose_allow_add(phone, label)


@card_tool
async def propose_remove_allowed_number(phone: str):
    """Prepare to remove a number's call approval.

    Args:
        phone: E.164.
    """
    return await actions.propose_allow_remove(phone)


@card_tool
async def propose_cancel_callback(callback_id: str):
    """Prepare to cancel a callback the patient scheduled by text.

    Args:
        callback_id: From show_sms_followups.
    """
    return await actions.propose_cancel_callback(callback_id)


@card_tool
async def propose_set_cooldown(enabled: bool):
    """Prepare to turn the 24-hour re-text limit on or off.

    Args:
        enabled: True to turn the limit on.
    """
    return await actions.propose_cooldown(enabled)


ALL = [
    to_be_called,
    called_today,
    needs_follow_up,
    cleared_patients,
    propose_change_follow_up,
    propose_cancel_call,
    reschedule_call,
    patient_history,
    list_calls,
    follow_up_queue,
    get_call,
    get_transcript,
    show_call_list,
    show_sms_followups,
    show_allowlist,
    show_help,
    propose_place_call,
    show_place_call_form,
    propose_redial,
    propose_add_to_call_list,
    propose_remove_from_call_list,
    propose_allow_number,
    propose_remove_allowed_number,
    propose_cancel_callback,
    propose_set_cooldown,
]


def build_tools() -> list[FunctionTool]:
    return [FunctionTool(fn) for fn in ALL]
