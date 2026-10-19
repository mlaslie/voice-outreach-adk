"""Dry-run SMS simulator: play the text follow-up end to end from the dashboard.

A nurse or tester picks an allowlisted number and plays the patient. ``start``
stages what a real missed call would have left behind -- a call doc, an SMS
consent and a fresh conversation -- for the FICTIONAL test patient, then sends
the invite at once. ``reply`` feeds a typed message through the same inbound
processing a real Twilio message gets (``sms_service.handle_inbound``), so the
menu, the DOB gate, the questionnaire, triage and report filing are the real
code, and the filed report lands on the staged call like any by-text report.

Guards (each one is enough on its own to keep a text off a phone):

* ``sms.dry_run`` must be on AND the live provider must be ``DryRunSms``;
  anything else is a 409, checked before every start and every reply.
* the number must be E.164 and on the dial allowlist (same check as the dialer).
* ``reply`` only continues a conversation the simulator started: it cannot
  inject a message into a real patient's session.
* STOP is refused: it would withdraw consent and remove the number from the
  dial allowlist for real.
* The patient context carries ``SIMULATOR_FLAG``, which ``sms_service._place``
  refuses, so "call me now" or a booked time never rings a phone.
* The consent is recorded with ``source = SIMULATOR_SOURCE`` and only counts
  while dry-run is on (``sms_service.active_consent``).

Logs carry masked numbers and no message bodies.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from voa_core import copy as messages
from voa_core import intent
from voa_core.logging import mask_phone
from voa_core.sms import states as st

from . import sms_service as sms
from .context import CALLS, Api
from .dialer import DialError, check_allowed

log = logging.getLogger(__name__)

PLACED_VIA = "sms_simulator"
CALL_PREFIX = "smstest_"
STATEMENT_VERSION = "sms-simulator/dry-run"

# Fictional. Never a real person.
TEST_PATIENT = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "patient_name": "Jordan Rivera",
    "patient_id": "TEST-0001",
    "patient_dob": "1950-03-15",
}


class SimulatorError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _require_dry_run(api: Api) -> None:
    if not api.cfg.sms.dry_run:
        raise SimulatorError(409, "The SMS simulator only runs while SMS is in dry-run mode.")
    if not api.cfg.sms.enabled:
        raise SimulatorError(409, "SMS follow-up is disabled.")
    prov = sms.provider(api)
    if getattr(prov, "name", None) != "dry_run":
        raise SimulatorError(409, "The SMS provider is not the dry-run provider; refusing.")


async def _require_number(api: Api, phone: str) -> None:
    if not sms.is_e164(phone):
        raise SimulatorError(400, "phone must be E.164.")
    try:
        await check_allowed(api, phone)
    except DialError as exc:
        raise SimulatorError(exc.status, exc.detail) from None


def is_simulator_session(session: dict | None) -> bool:
    return bool(((session or {}).get("patient_context") or {}).get(sms.SIMULATOR_FLAG))


async def thread(api: Api, phone: str, call_id: str) -> list[dict]:
    return await sms.list_messages(api, phone, call_sid=call_id)


async def start(api: Api, phone: str, *, diabetic: bool, by: str) -> dict[str, Any]:
    """Stage a missed call for the test patient and send the invite now."""
    _require_dry_run(api)
    await _require_number(api, phone)
    if await sms.is_opted_out(api, phone):
        raise SimulatorError(409, "This number has opted out of texts (STOP).")

    current = await sms.get_session(api, phone)
    if current and not is_simulator_session(current):
        state = current.get("state")
        if state in st.SURVEY_STATES and not current.get("survey_submitted_at"):
            raise SimulatorError(409, "A real by-text questionnaire is in progress on this number.")
        if current.get("callback_id") and (current.get("scheduled_for") or "") > sms._ts(api):
            raise SimulatorError(409, "A real callback is booked for this number.")

    consent = "existing"
    if await sms.active_consent(api, phone) is None:
        await sms.grant_consent(
            api,
            phone,
            statement_version=STATEMENT_VERSION,
            granted_by=by,
            source=sms.SIMULATOR_SOURCE,
        )
        consent = "simulator"

    call_id = CALL_PREFIX + uuid.uuid4().hex[:16]
    now = sms._ts(api)
    ctx = {**TEST_PATIENT, "is_diabetic": bool(diabetic), sms.SIMULATOR_FLAG: True}
    await api.store.set(
        CALLS,
        call_id,
        {
            "call_sid": call_id,
            "to": phone,
            "from": "sms_simulator",
            "call_status": "rna",
            "initiated_at": now,
            "updated_at": now,
            "patient_id": TEST_PATIENT["patient_id"],
            "patient_name": TEST_PATIENT["patient_name"],
            "patient_context": {k: v for k, v in ctx.items() if k != "patient_dob"},
            "placed_via": PLACED_VIA,
            "amd_requested": False,
            "channel": "sms",
            "test": True,
        },
    )
    # The same conversation send_invite opens, minus cooldown and quiet hours.
    await api.store.set(
        sms.SESSIONS,
        phone,
        {
            "phone": phone,
            "call_sid": call_id,
            "origin_entry_id": None,
            "patient_context": ctx,
            "patient_name": TEST_PATIENT["patient_name"],
            "patient_id": TEST_PATIENT["patient_id"],
            "state": st.STATE_AWAITING_CHOICE,
            "retry_count": 0,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
            "last_invite_at": now,
            "last_inbound_at": None,
            "expires_at": sms._ts(api, api.now() + timedelta(hours=api.cfg.sms.session_ttl_h)),
            "callback_id": None,
            "scheduled_for": None,
            "scheduled_for_text": None,
            "last_error": None,
        },
    )
    try:
        # Not ``unsolicited``: the simulator ignores quiet hours (nothing is sent).
        await sms.send_sms(
            api,
            phone,
            messages.invite(TEST_PATIENT["patient_name"], sms.site_copy(api)),
            call_id=call_id,
        )
    except sms.SendBlocked as exc:
        await sms._patch_quietly(
            api, phone, {"state": st.STATE_CLOSED, "last_error": exc.reason[:300]}
        )
        raise SimulatorError(503 if exc.retryable else 409, exc.reason) from None
    log.info(
        "sms simulator started",
        extra={
            "event": "sms.simulator_start",
            "to": mask_phone(phone),
            "consent": consent,
            "diabetic": bool(diabetic),
        },
    )
    return {
        "phone": phone,
        "call_sid": call_id,
        "dry_run": True,
        "survey_enabled": api.cfg.sms.survey_enabled,
        "consent": consent,
        "patient": {
            "patient_name": TEST_PATIENT["patient_name"],
            "patient_id": TEST_PATIENT["patient_id"],
            "patient_dob": TEST_PATIENT["patient_dob"],
            "is_diabetic": bool(diabetic),
        },
        "state": st.STATE_AWAITING_CHOICE,
        "messages": await thread(api, phone, call_id),
    }


async def reply(api: Api, phone: str, body: str) -> dict[str, Any]:
    """One message from the 'patient', through the real inbound handler."""
    _require_dry_run(api)
    await _require_number(api, phone)
    body = (body or "").strip()
    if not body:
        raise SimulatorError(400, "Type a reply.")
    session = await sms.get_session(api, phone)
    if not is_simulator_session(session):
        raise SimulatorError(409, "No simulator conversation on this number; start one first.")
    if intent.classify_compliance(body) is intent.Intent.STOP:
        raise SimulatorError(
            400,
            "STOP is not simulated: it would opt this number out and remove it from the "
            "calling list for real.",
        )
    call_id = str(session.get("call_sid") or "")
    before = {m["id"] for m in await thread(api, phone, call_id)}
    message_id = "SMsim" + uuid.uuid4().hex
    try:
        result = await sms.handle_inbound(api, phone, body, message_id)
    except sms.Retryable as exc:
        raise SimulatorError(503, str(exc)) from None
    messages_now = await thread(api, phone, call_id)
    after = await sms.get_session(api, phone) or {}
    log.info(
        "sms simulator reply",
        extra={
            "event": "sms.simulator_reply",
            "to": mask_phone(phone),
            "note": result.get("note"),
        },
    )
    return {
        "phone": phone,
        "call_sid": call_id,
        "result": {k: result.get(k) for k in ("note", "effect", "replies") if k in result},
        "state": after.get("state"),
        "report_id": after.get("report_id"),
        "new_messages": [
            m for m in messages_now if m["id"] not in before and m["direction"] == "outbound"
        ],
        "messages": messages_now,
    }
