"""SMS simulator: play the text follow-up end to end from the dashboard.

A nurse or tester picks an allowlisted number and plays the patient. ``start``
stages what a real missed call would have left behind -- a call doc, an SMS
consent and a fresh conversation -- for the FICTIONAL test patient, then sends
the invite at once. ``reply`` feeds a typed message through the same inbound
processing a real Twilio message gets (``sms_service.handle_inbound``), so the
menu, the DOB gate, the questionnaire, triage and report filing are the real
code.

It runs whatever ``sms.dry_run`` says (22 Oct): a simulator conversation never
runs on the real ``Api``. It runs on :func:`simulator_api`, a copy of it in which

* the SMS provider is ``DryRunSms`` and ``sms.dry_run`` is on, so every text the
  conversation produces stops at the dry-run provider, never Twilio;
* the store is :class:`SimulatorStore`: every collection is renamed into the
  ``simulator__`` namespace (sessions, messages, consents, opt-outs, callbacks,
  filings, the hourly budget, the staged call, its report, ``ehr_outbox``...),
  except a few configuration collections it may READ (the dial allowlist, the
  company name, the SMS switches) and can never write;
* the task queue drops everything (no recall dial, no booked callback, no
  safety-net filing, no translation or knowledge job ever reaches Cloud Tasks),
  and there is no voice provider.

So the two worlds cannot meet on a phone number: real conversations are keyed by
the phone in ``sms_sessions``, the simulator's by the phone in
``simulator__sms_sessions``. A real inbound text (Twilio webhook -> task ->
``handle_inbound(api, ...)``) only ever reads the real collection, and
``reply`` only ever reads the simulator's.

Guards kept from the dry-run days, now belt and braces: the number must be E.164
and on the dial allowlist (the simulator's sends pass the real ``send_sms``
checks, which read the real allowlist); the patient context carries
``SIMULATOR_FLAG``, which ``sms_service._place`` refuses; a simulator consent
(``source = SIMULATOR_SOURCE``) counts only in dry-run. STOP is simulated: it
opts the number out inside the namespace only (its attempt to remove the number
from the real allowlist is refused by the store and logged).

Languages (docs/LANGUAGES-PLAN.md step 5): ``start(language=...)`` plays a
patient whose language is one of the 24. A non-English conversation uses the
language pack's SMS wording, and may use a **draft** pack: nothing is sent, and
the response says so (``wording.approved`` false, "Draft wording, not
approved"). ``sms_service.send_sms`` allows a draft only for this simulator's
own session in dry-run with the dry-run provider; every real send refuses one.
The simulated patient's replies take the real inbound path, so STOP/HELP and
red flags are checked on the original, then the text is translated to English
(the configured translator, Gemini when deployed) before the state machine
reads it.

Logs carry masked numbers and no message bodies.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from datetime import timedelta
from typing import Any

from voa_core import language_packs, languages, organization
from voa_core.logging import mask_phone
from voa_core.sms import states as st
from voa_providers.sms import DryRunSms

from . import sms_service as sms
from .context import ALLOWLIST, CALLS, REPORTS, Api
from .dialer import DialError, check_allowed

log = logging.getLogger(__name__)

PLACED_VIA = "sms_simulator"
CALL_PREFIX = "smstest_"
STATEMENT_VERSION = "sms-simulator/dry-run"

#: Every collection the simulator writes lives under this prefix.
NAMESPACE = "simulator__"
#: Real configuration a simulator conversation reads (never writes): the dial
#: allowlist (a test runs only on an approved number), the company name in the
#: texts (``call_settings/organization``) and the SMS runtime switches.
READ_THROUGH = frozenset({ALLOWLIST, organization.COLLECTION, sms.SETTINGS})

# Fictional. Never a real person.
TEST_PATIENT = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "patient_name": "Jordan Rivera",
    "patient_id": "TEST-0001",
    "patient_dob": "1950-03-15",
}


class SimulatorWriteRefused(PermissionError):
    """A simulator conversation tried to write a real (read-through) collection."""


class SimulatorStore:
    """The real store, seen through the simulator's namespace.

    ``coll`` becomes ``simulator__coll`` for every read and write, so nothing the
    simulator does lands in (or is read from) a real collection, except the
    :data:`READ_THROUGH` configuration, which it reads from the real collection
    and may never write (:class:`SimulatorWriteRefused`)."""

    def __init__(self, inner) -> None:
        self.inner = inner

    @staticmethod
    def _read(coll: str) -> str:
        return coll if coll in READ_THROUGH else NAMESPACE + coll

    @staticmethod
    def _write(coll: str) -> str:
        if coll in READ_THROUGH:
            log.warning(
                "simulator write to a real collection refused",
                extra={"event": "sms.simulator_write_refused", "collection": coll},
            )
            raise SimulatorWriteRefused(f"the SMS simulator may not write {coll}")
        return NAMESPACE + coll

    async def get(self, coll, doc_id):
        return await self.inner.get(self._read(coll), doc_id)

    async def query(self, coll, where=None, order_by=None, descending=False, limit=None):
        return await self.inner.query(
            self._read(coll), where, order_by=order_by, descending=descending, limit=limit
        )

    def watch(self, coll, doc_id):
        return self.inner.watch(self._read(coll), doc_id)

    async def set(self, coll, doc_id, data):
        return await self.inner.set(self._write(coll), doc_id, data)

    async def merge(self, coll, doc_id, data):
        return await self.inner.merge(self._write(coll), doc_id, data)

    async def delete(self, coll, doc_id):
        return await self.inner.delete(self._write(coll), doc_id)

    async def create(self, coll, doc_id, data):
        return await self.inner.create(self._write(coll), doc_id, data)

    async def transact(self, coll, doc_id, fn):
        return await self.inner.transact(self._write(coll), doc_id, fn)


class DroppedTasks:
    """The simulator's task queue: nothing reaches Cloud Tasks. A recall dial, a
    booked callback, the survey safety net, translation and knowledge jobs are
    all dropped (the filing itself runs inline). The last few are kept in memory
    for tests; payloads are fictional."""

    def __init__(self) -> None:
        self.dropped: deque[dict] = deque(maxlen=50)

    async def enqueue(self, queue, path, payload, *, name=None, delay_s=0):
        self.dropped.append({"queue": queue, "path": path, "payload": payload, "name": name})
        log.info(
            "simulator task dropped",
            extra={"event": "sms.simulator_task_dropped", "kind": (payload or {}).get("kind")},
        )
        return True


def simulator_api(api: Api) -> Api:
    """The ``Api`` every simulator conversation runs on (built once per ``api``).

    Same configuration except ``sms.dry_run`` (always on), the dry-run SMS
    provider, the namespaced store, a task queue that drops everything and no
    voice provider. The clock, translator and time interpreter follow ``api``."""
    sim = getattr(api, "_sms_simulator_api", None)
    if sim is None or sim.store.inner is not api.store:
        cfg = api.cfg.model_copy(update={"sms": api.cfg.sms.model_copy(update={"dry_run": True})})
        sim = Api(cfg=cfg, store=SimulatorStore(api.store), tasks=DroppedTasks(), voice=None)
        sim.sms = DryRunSms()
        api._sms_simulator_api = sim
    sim.clock = api.clock
    sim.translator = api.get_translator()
    if hasattr(api, "sms_interpreter"):
        sim.sms_interpreter = api.sms_interpreter
    return sim


class SimulatorError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _require_enabled(api: Api, sim: Api) -> None:
    if not api.cfg.sms.enabled:
        raise SimulatorError(409, "SMS follow-up is disabled.")
    # Never expected: simulator_api always installs DryRunSms. Checked anyway,
    # before every start and every reply.
    if getattr(sms.provider(sim), "name", None) != "dry_run" or not sim.cfg.sms.dry_run:
        raise SimulatorError(500, "The simulator is not on the dry-run provider; refusing.")


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


DRAFT_LABEL = "Draft wording, not approved"


def _language(language: str | None) -> tuple[str | None, language_packs.Pack | None]:
    """(code, pack) for a non-English simulation; (None, None) for English.
    Raises SimulatorError for an unknown language or one with no complete SMS
    wording (a draft is allowed here: nothing is sent)."""
    code = languages.normalize(language) if language else languages.DEFAULT
    if code is None:
        raise SimulatorError(400, languages.unsupported_message(language))
    if code in languages.ENGLISH:
        return None, None
    pack = language_packs.sms_pack(None, code, allow_drafts=True)
    if pack is None:
        raise SimulatorError(
            409, f"There is no {languages.name(code)} text wording yet, so it cannot be tested."
        )
    return code, pack


def _wording(code: str | None, pack: language_packs.Pack | None) -> dict[str, Any]:
    if code is None or pack is None:
        return {"language": languages.DEFAULT, "status": "approved", "approved": True, "label": ""}
    return {
        "language": code,
        "name": languages.name(code),
        "status": pack.status,
        "approved": pack.approved,
        "label": "" if pack.approved else DRAFT_LABEL,
    }


async def start(
    api: Api, phone: str, *, diabetic: bool, by: str, language: str | None = None
) -> dict[str, Any]:
    """Stage a missed call for the test patient and send the invite now.

    Everything is written through :func:`simulator_api`: the real collections
    are only read (the allowlist, the company name, the real opt-out)."""
    sim = simulator_api(api)
    _require_enabled(api, sim)
    code, pack = _language(language)
    await _require_number(api, phone)
    await sms.refresh_org(sim)
    # A number that really texted STOP stays out of tests too (read only).
    if await sms.is_opted_out(api, phone):
        raise SimulatorError(409, "This number has opted out of texts (STOP).")
    # A STOP played in an earlier test opted the number out of the simulator
    # only; a new test starts clean.
    await sim.store.delete(sms.OPTOUTS, phone)

    consent = "existing"
    if await sms.active_consent(sim, phone) is None:
        await sms.grant_consent(
            sim,
            phone,
            statement_version=STATEMENT_VERSION,
            granted_by=by,
            source=sms.SIMULATOR_SOURCE,
        )
        consent = "simulator"

    call_id = CALL_PREFIX + uuid.uuid4().hex[:16]
    now = sms._ts(sim)
    ctx = {**TEST_PATIENT, "is_diabetic": bool(diabetic), sms.SIMULATOR_FLAG: True}
    if code:
        ctx.update(language=code, call_in_english=False)
    await sim.store.set(
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
            "started_by": by,
            **({"language": code} if code else {}),
        },
    )
    # The same conversation send_invite opens, minus cooldown and quiet hours.
    await sim.store.set(
        sms.SESSIONS,
        phone,
        {
            "phone": phone,
            "call_sid": call_id,
            "origin_entry_id": None,
            "patient_context": ctx,
            "patient_name": TEST_PATIENT["patient_name"],
            "patient_id": TEST_PATIENT["patient_id"],
            **(
                {"sms_language": code, "sms_draft_wording": not pack.approved}
                if code and pack
                else {}
            ),
            "state": st.STATE_AWAITING_CHOICE,
            "retry_count": 0,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
            "last_invite_at": now,
            "last_inbound_at": None,
            "expires_at": sms._ts(sim, sim.now() + timedelta(hours=sim.cfg.sms.session_ttl_h)),
            "callback_id": None,
            "scheduled_for": None,
            "scheduled_for_text": None,
            "last_error": None,
        },
    )
    try:
        # Not ``unsolicited``: the simulator ignores quiet hours (nothing is sent).
        session = await sms.get_session(sim, phone)
        await sms.send_sms(
            sim,
            phone,
            sms.session_copy(session).invite(TEST_PATIENT["patient_name"], sms.site_copy(sim)),
            call_id=call_id,
        )
    except sms.SendBlocked as exc:
        await sms._patch_quietly(
            sim, phone, {"state": st.STATE_CLOSED, "last_error": exc.reason[:300]}
        )
        raise SimulatorError(503 if exc.retryable else 409, exc.reason) from None
    log.info(
        "sms simulator started",
        extra={
            "event": "sms.simulator_start",
            "to": mask_phone(phone),
            "consent": consent,
            "diabetic": bool(diabetic),
            "language": code or languages.DEFAULT,
        },
    )
    return {
        "phone": phone,
        "call_sid": call_id,
        "dry_run": True,
        "survey_enabled": sim.cfg.sms.survey_enabled,
        "consent": consent,
        "wording": _wording(code, pack),
        "patient": {
            "patient_name": TEST_PATIENT["patient_name"],
            "patient_id": TEST_PATIENT["patient_id"],
            "patient_dob": TEST_PATIENT["patient_dob"],
            "is_diabetic": bool(diabetic),
        },
        "state": st.STATE_AWAITING_CHOICE,
        "messages": await thread(sim, phone, call_id),
    }


async def _report(sim: Api, report_id: str | None) -> dict[str, Any] | None:
    """What the dialog shows of the filed (simulator-only) report."""
    if not report_id:
        return None
    doc = await sim.store.get(REPORTS, report_id) or {}
    return {
        "report_id": report_id,
        "follow_up": doc.get("follow_up"),
        "follow_up_reason": doc.get("follow_up_reason"),
    }


async def reply(api: Api, phone: str, body: str) -> dict[str, Any]:
    """One message from the 'patient', through the real inbound handler, on the
    simulator's side only: it can only address a conversation the simulator
    started (the namespace holds nothing else)."""
    sim = simulator_api(api)
    _require_enabled(api, sim)
    await _require_number(api, phone)
    body = (body or "").strip()
    if not body:
        raise SimulatorError(400, "Type a reply.")
    session = await sms.get_session(sim, phone)
    if not is_simulator_session(session):
        raise SimulatorError(409, "No simulator conversation on this number; start one first.")
    call_id = str(session.get("call_sid") or "")
    before = {m["id"] for m in await thread(sim, phone, call_id)}
    message_id = "SMsim" + uuid.uuid4().hex
    try:
        result = await sms.handle_inbound(sim, phone, body, message_id)
    except sms.Retryable as exc:
        raise SimulatorError(503, str(exc)) from None
    messages_now = await thread(sim, phone, call_id)
    after = await sms.get_session(sim, phone) or {}
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
        "report": await _report(sim, after.get("report_id")),
        "new_messages": [
            m for m in messages_now if m["id"] not in before and m["direction"] == "outbound"
        ],
        "messages": messages_now,
    }
