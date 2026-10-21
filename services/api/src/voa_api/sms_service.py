"""SMS follow-up: the stateful half (port of the old sidecar's main/store/sender).

The pure half lives in ``voa_core.sms.conversation`` (what to do about one
message) and ``voa_agents.sms`` (that, plus a confirmed-only Gemini reading of
free-text times). Everything here is I/O: sessions, consent, opt-outs, the one
send choke point, bookings, dials and report filing.

Collections
-----------
==============================  =================================================
``sms_sessions/{phone}``        one live conversation per handset
``sms_messages/{id}``           inbound + outbound thread for the dashboard
``sms_consents/{id}``           APPEND-ONLY consent history; withdrawal stamps
                                ``withdrawn_at`` and never deletes (review S-6)
``sms_optouts/{phone}``         STOP list with its own event history
``scheduled_callbacks/{id}``    patient-chosen callback times
``sms_settings/runtime``        operator switches (invite cooldown)
``sms_inbound_seen/{msg id}``   inbound de-duplication (review C-7)
``sms_filings/{filing id}``     idempotency record for a by-text report (S-1)
==============================  =================================================

Work handoff is Cloud Tasks on ``cfg.services.queue_sms`` instead of the old
ticker and lease queue: invites and bookings are delayed, named tasks, and
retries come from Cloud Tasks. A handler signals "try again" by raising
:class:`Retryable` (the route answers 503) and "never again" by returning
normally (2xx).

Two ordering rules carried over from the old service, because each was learned
from an incident:

* **Persist before replying.** A promise ("we'll call you shortly", "you're set
  for Thu 3 PM") goes out only after the thing promised is durably queued.
* **Claim before dialling.** A dial handler moves its target to ``dialing`` in
  a transaction first; a retried or duplicated task finds it claimed and stops.
  One missed confirmation is recoverable; a second phone call is not.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from voa_core import budget, compliance, lifecycle, reports, review, survey, timeparse
from voa_core import copy as messages
from voa_core.config import resolve_secret
from voa_core.copy import SiteCopy
from voa_core.logging import mask_phone
from voa_core.sms import states as st
from voa_core.sms.conversation import (
    ConversationPolicy,
    Decision,
    Effect,
    asked_questions,
    campaign_of,
    session_campaign_id,
)
from voa_core.store import Where, new_id, now_iso
from voa_core.timeparse import SchedulingPolicy

from .context import ALLOWLIST, CALL_ID_RE, CALL_LIST, CALLS, EHR_OUTBOX, REPORTS, Api
from .dialer import DialError, check_allowed, dial
from .models import PlaceCallRequest

log = logging.getLogger(__name__)

SESSIONS = "sms_sessions"
MESSAGES = "sms_messages"
CONSENTS = "sms_consents"
OPTOUTS = "sms_optouts"
CALLBACKS = "scheduled_callbacks"
SETTINGS = "sms_settings"
SETTINGS_DOC = "runtime"
INBOUND_SEEN = "sms_inbound_seen"
FILINGS = "sms_filings"

BUDGET_NAME = "sms_sends"
ACTION_PATH = "/tasks/sms-action"

#: ``expires_at`` of every terminal session. Sorts after any real timestamp, so
#: the expiry query (``expires_at <= now``) only ever sees live sessions and can
#: never be filled up by dead rows (review C-13).
NEVER = "9999-12-31T23:59:59.999+00:00"

TERMINAL_STATES = frozenset({st.STATE_CLOSED, st.STATE_EXPIRED, st.STATE_OPTED_OUT})
#: Expirable: the live states plus "dialing" (a crash mid-dial must not pin a
#: session open forever).
EXPIRABLE_STATES = st.LIVE_STATES | {st.STATE_DIALING}
UNANSWERED = frozenset({lifecycle.RNA, lifecycle.BUSY, lifecycle.LEFT_VMAIL})

#: Runtime switches: name -> the value used when unset or unreadable (safe side).
SETTING_DEFAULTS: dict[str, bool] = {"invite_cooldown_enabled": True}

#: The queued copy of a survey filing runs this long after the inline attempt,
#: so the two never race to send the closing message.
SURVEY_SAFETY_NET_DELAY_S = 60
INBOUND_DEDUPE_DAYS = 7
MAX_INBOUND_CHARS = 1600
DOB_REDACTED = "[date of birth reply - not stored]"
_REQUIRED_DIAL_CONTEXT = ("patient_name", "patient_id", "patient_dob")
_TASK_NAME_OK = re.compile(r"[^A-Za-z0-9_-]")
# The dry-run simulator (sms_simulator.py): consent source and patient-context flag.
SIMULATOR_SOURCE = "sms_simulator"
SIMULATOR_FLAG = "sms_simulator"


# --------------------------------------------------------------------------- errors


class SendBlocked(Exception):
    """Refused before anything left the building (or the provider refused).

    ``retryable``: the same send may succeed later. ``resume_at``: refused for
    quiet hours, and this is when the window opens.
    """

    def __init__(self, reason: str, *, retryable: bool = False, resume_at: datetime | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.resume_at = resume_at


class Retryable(Exception):
    """The handler failed in a way worth retrying: the route answers 503."""


# --------------------------------------------------------------------------- config


def site_copy(api: Api) -> SiteCopy:
    from voa_core import organization

    return SiteCopy(
        # The admin console's company name (refreshed by refresh_org at each entry point).
        org_name=organization.cached(api.store, api.cfg.site.org_name),
        support_email=api.cfg.site.support_email,
        voice_number=api.cfg.sms.voice_number,
        survey_enabled=api.cfg.sms.survey_enabled,
    )


async def refresh_org(api: Api) -> None:
    """Load the current company name before building any patient-facing text."""
    from voa_core import organization

    await organization.org_name(api.store, api.cfg.site.org_name)


def scheduling(api: Api) -> SchedulingPolicy:
    s = api.cfg.sms
    return SchedulingPolicy(
        tz=api.cfg.site.timezone,
        quiet_start=s.quiet_start_hour,
        quiet_end=s.quiet_end_hour,
        max_days=s.max_schedule_days,
    )


def policy(api: Api) -> ConversationPolicy:
    return ConversationPolicy(
        survey_enabled=api.cfg.sms.survey_enabled,
        reply_to_compliance_keywords=api.cfg.sms.reply_to_compliance_keywords,
    )


def local_now(api: Api) -> datetime:
    return api.now().astimezone(ZoneInfo(api.cfg.site.timezone))


def _ts(api: Api, dt: datetime | None = None) -> str:
    return now_iso(dt or api.now())


def is_e164(phone: str | None) -> bool:
    return bool(compliance.E164_RE.match(phone or ""))


def _task_name(*parts: str) -> str:
    return _TASK_NAME_OK.sub("-", "-".join(parts))[:400]


# --------------------------------------------------------------------------- wiring
#
# context.py predates this module, so the provider and the interpreter hang off
# the Api instance as plain attributes, built on first use. Tests set them first.


def provider(api: Api):
    if not hasattr(api, "sms"):
        api.sms = build_provider(api)
    return api.sms


def build_provider(api: Api):
    from voa_providers.sms import DryRunSms, FakeSms, TwilioSms

    cfg = api.cfg
    if cfg.sms.dry_run:
        return DryRunSms()
    if cfg.telephony.sms_provider == "fake":
        return FakeSms()
    tw = cfg.providers.get("twilio", {})
    try:
        sec = {
            k: resolve_secret(tw.get(k, ""), cfg.gcp.project_id)
            for k in ("account_sid", "api_key_sid", "api_key_secret")
        }
        from_number = cfg.sms.from_number or tw.get("from_number", "")
        if not all(sec.values()) or not from_number:
            raise ValueError("incomplete twilio config")
        return TwilioSms(from_number=from_number, **sec)
    except Exception:
        log.warning(
            "sms provider not configured; sending disabled",
            extra={"event": "sms.not_configured"},
        )
        return None


_UNSET = object()


def conversation(api: Api):
    from voa_agents.sms import SmsConversation, build_time_interpreter

    interp = getattr(api, "sms_interpreter", _UNSET)
    if interp is _UNSET:
        interp = None
        # Never reach a model from a test run, whatever the config says.
        if api.cfg.env != "test":
            try:
                interp = build_time_interpreter(api.cfg)
            except Exception:
                log.warning("time interpreter unavailable", extra={"event": "sms.llm_disabled"})
        api.sms_interpreter = interp
    return SmsConversation(
        site=site_copy(api), policy=policy(api), scheduling=scheduling(api), interpreter=interp
    )


async def _enqueue(api: Api, payload: dict, *, name: str, delay_s: float = 0) -> bool:
    return await api.tasks.enqueue(
        api.cfg.services.queue_sms, ACTION_PATH, payload, name=name, delay_s=max(0.0, delay_s)
    )


# --------------------------------------------------------------------------- sessions


async def get_session(api: Api, phone: str) -> dict | None:
    doc = await api.store.get(SESSIONS, phone)
    return {"phone": phone, **doc} if doc is not None else None


async def patch_session(api: Api, phone: str, patch: dict) -> dict | None:
    """Merge fields; never resurrect a deleted session. Raises on a store error
    (callers that can tolerate a lost write catch it themselves).

    Every move into a terminal state also sets ``expires_at`` to :data:`NEVER`,
    so the expiry scan only ever reads live rows (C-13).
    """
    patch = {**patch, "updated_at": _ts(api)}
    if patch.get("state") in TERMINAL_STATES:
        patch["expires_at"] = NEVER

    def mutate(cur):
        return None if cur is None else {**cur, **patch}

    return await api.store.transact(SESSIONS, phone, mutate)


async def _patch_required(api: Api, phone: str, patch: dict) -> dict:
    doc = await patch_session(api, phone, patch)
    if doc is None:
        raise RuntimeError("session vanished mid-flight")
    return doc


async def _patch_quietly(api: Api, phone: str, patch: dict) -> None:
    try:
        await patch_session(api, phone, patch)
    except Exception:
        log.warning("session bookkeeping failed", extra={"event": "sms.session_write_failed"})


def dialable(ctx: dict | None) -> bool:
    """Whether we hold enough to place a call. The voice agent verifies identity
    with the DOB, so a call without one would reach the patient and then fail."""
    return bool(ctx) and all(str(ctx.get(f) or "").strip() for f in _REQUIRED_DIAL_CONTEXT)


# --------------------------------------------------------------------------- settings


async def get_settings(api: Api) -> dict[str, bool]:
    try:
        doc = await api.store.get(SETTINGS, SETTINGS_DOC) or {}
    except Exception:
        log.warning("settings unreadable; using defaults", extra={"event": "sms.settings_read"})
        return dict(SETTING_DEFAULTS)
    return {k: bool(doc.get(k, v)) for k, v in SETTING_DEFAULTS.items()}


async def set_setting(api: Api, name: str, value: bool, *, by: str) -> dict[str, bool]:
    if name not in SETTING_DEFAULTS:
        raise KeyError(name)
    await api.store.merge(
        SETTINGS, SETTINGS_DOC, {name: bool(value), "updated_at": _ts(api), "updated_by": by}
    )
    log.warning(
        "sms runtime setting changed",
        extra={"event": "sms.setting", "setting": name, "value": bool(value)},
    )
    return await get_settings(api)


# --------------------------------------------------------------------------- consent


async def consent_history(api: Api, phone: str) -> list[dict]:
    rows = [d for _, d in await api.store.query(CONSENTS, [Where("phone", "==", phone)])]
    return sorted(rows, key=lambda d: d.get("granted_at") or "")


async def active_consent(api: Api, phone: str) -> dict | None:
    """The newest grant not yet withdrawn. Raises on a read error (callers refuse).

    A grant recorded by the dry-run simulator (``source == SIMULATOR_SOURCE``)
    only counts while ``sms.dry_run`` is on: a test conversation must never
    become the consent that lets a real text out once sending is switched on.
    """
    live = [
        d
        for d in await consent_history(api, phone)
        if not d.get("withdrawn_at")
        and (api.cfg.sms.dry_run or d.get("source") != SIMULATOR_SOURCE)
    ]
    return live[-1] if live else None


async def grant_consent(
    api: Api, phone: str, *, statement_version: str, granted_by: str, source: str
) -> dict:
    """Append one grant. Never overwrites an earlier record."""
    consent_id = new_id()
    doc = {
        "consent_id": consent_id,
        "phone": phone,
        "granted_at": _ts(api),
        "statement_version": statement_version,
        "granted_by": granted_by,
        "source": source,
        "withdrawn_at": None,
        "withdrawn_by": None,
        "withdrawn_reason": None,
    }
    await api.store.set(CONSENTS, consent_id, doc)
    return doc


async def withdraw_consent(api: Api, phone: str, *, by: str, reason: str) -> int:
    """Stamp ``withdrawn_at`` on every live grant (review S-6: a TCPA dispute turns
    on proving consent existed at send time, so the record is never deleted)."""
    now = _ts(api)
    n = 0
    for consent_id, d in await api.store.query(CONSENTS, [Where("phone", "==", phone)]):
        if d.get("withdrawn_at"):
            continue

        def mutate(cur):
            if cur is None or cur.get("withdrawn_at"):
                return None
            return {**cur, "withdrawn_at": now, "withdrawn_by": by, "withdrawn_reason": reason}

        await api.store.transact(CONSENTS, consent_id, mutate)
        n += 1
    return n


def start_statement_version(api: Api) -> str:
    """Which registered copy a START re-consent was given against (S-6). Derived
    from the compliance copy itself, so a copy change produces a new version."""
    site = site_copy(api)
    text = messages.help_message(site) + "\n" + messages.opted_out(site)
    return "start-keyword/" + hashlib.sha256(text.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- opt-outs


async def is_opted_out(api: Api, phone: str) -> bool:
    """Raises on a read failure: an opt-out check that fails open texts (or rings)
    someone who asked us not to."""
    doc = await api.store.get(OPTOUTS, phone)
    return bool(doc and doc.get("opted_out", True))


async def record_opt_out(
    api: Api, phone: str, *, source: str, set_session_state: bool = False
) -> dict[str, Any]:
    """STOP: stop texts AND calls.

    * the opt-out record (with history; START flips it, never deletes it);
    * consent withdrawn (stamped, not deleted);
    * every scheduled callback for the handset cancelled;
    * the number removed from the dial allowlist, so the call-list runner cannot
      ring them either. A number pinned by the static (env) allowlist cannot be
      removed here; that is logged loudly rather than worked around.

    Deliberately NOT cancelled: a queued survey filing. STOP means "stop
    contacting me", not "discard what I told you".
    """
    now = _ts(api)

    def mutate(cur):
        hist = [*((cur or {}).get("history") or []), {"at": now, "event": "stop", "by": source}]
        return {
            "phone": phone,
            "opted_out": True,
            "opted_out_at": now,
            "source": source,
            "history": hist[-50:],
        }

    await api.store.transact(OPTOUTS, phone, mutate)
    out: dict[str, Any] = {}
    try:
        out["consents_withdrawn"] = await withdraw_consent(
            api, phone, by=source, reason="opted out"
        )
    except Exception:
        log.warning("consent withdrawal failed", extra={"event": "sms.consent_withdraw_failed"})
    out["callbacks_canceled"] = await cancel_callbacks(
        api, phone, status="canceled", reason="recipient opted out"
    )
    try:
        out["allowlist_removed"] = await api.store.delete(ALLOWLIST, phone)
    except Exception:
        out["allowlist_removed"] = False
        log.warning("allowlist removal failed", extra={"event": "sms.allowlist_remove_failed"})
    try:
        static = compliance.parse_allowlist(api.cfg.dialing.allowlist)
        pinned = static.mode is compliance.AllowlistMode.OPEN or static.matches(phone)
    except compliance.AllowlistConfigError:
        pinned = False
    if pinned:
        log.warning(
            "opted-out number is pinned by the static allowlist; calls are NOT suppressed",
            extra={"event": "sms.optout_static_allowlist", "to": mask_phone(phone)},
        )
    out["voice_suppressed"] = not pinned
    patch: dict[str, Any] = {"callback_id": None, "scheduled_for": None, "scheduled_for_text": None}
    if set_session_state:
        patch["state"] = st.STATE_OPTED_OUT
    await _patch_quietly(api, phone, patch)
    log.info(
        "opt-out recorded",
        extra={"event": "sms.opt_out", "to": mask_phone(phone), "via": source, **out},
    )
    return out


async def record_opt_in(api: Api, phone: str) -> None:
    """START: lift the opt-out and restore SMS consent, NOT the dial allowlist.

    Texting START is an affirmative opt-in to messages; consenting to messages is
    not consenting to calls, so re-adding a number to the calling list stays a
    deliberate operator action. Consent is only restored for a number that had
    consent before: START from a stranger creates no consent out of nothing.
    """
    now = _ts(api)

    def mutate(cur):
        if cur is None:
            return None
        hist = [*(cur.get("history") or []), {"at": now, "event": "start", "by": "sms:START"}]
        return {**cur, "opted_out": False, "opted_in_at": now, "history": hist[-50:]}

    await api.store.transact(OPTOUTS, phone, mutate)
    history = await consent_history(api, phone)
    if history and not any(not d.get("withdrawn_at") for d in history):
        await grant_consent(
            api,
            phone,
            statement_version=start_statement_version(api),
            granted_by="sms:START",
            source="start_keyword",
        )
    log.info("opt-in recorded", extra={"event": "sms.opt_in", "to": mask_phone(phone)})


# --------------------------------------------------------------------------- messages


async def log_message(
    api: Api,
    *,
    phone: str,
    direction: str,
    body: str,
    message_sid: str | None,
    call_sid: str | None,
) -> None:
    """Stored for the nurse-facing thread; never written to logs (bodies can carry
    clinical answers from the questionnaire onwards)."""
    at = _ts(api)
    try:
        await api.store.set(
            MESSAGES,
            f"{at}_{new_id()[:8]}",
            {
                "phone": phone,
                "direction": direction,
                "body": body,
                "message_sid": message_sid,
                "call_sid": call_sid,
                "at": at,
            },
        )
    except Exception:
        log.warning("message log write failed", extra={"event": "sms.log_failed"})


async def list_messages(api: Api, phone: str, call_sid: str | None = None) -> list[dict]:
    rows = [
        {"id": i, **d}
        for i, d in await api.store.query(MESSAGES, [Where("phone", "==", phone)], limit=1000)
    ]
    if call_sid:
        rows = [r for r in rows if r.get("call_sid") == call_sid]
    rows.sort(key=lambda r: r.get("at") or "")
    return [
        {
            "id": r["id"],
            "phone": r.get("phone", phone),
            "direction": r.get("direction"),
            "body": r.get("body", ""),
            "at": r.get("at", ""),
            "message_sid": r.get("message_sid"),
            "call_sid": r.get("call_sid"),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- the choke point


async def send_sms(
    api: Api,
    phone: str,
    body: str,
    *,
    call_id: str | None = None,
    unsolicited: bool = False,
    compliance_reply: bool = False,
) -> str:
    """Send one message. The ONLY place a message leaves this system.

    Order (free checks first; budget reserved before the provider and handed
    back whenever the provider is never reached):

    0. feature on, provider configured, E.164
    1. opt-out           (read failure refuses, retryable)
    2. SMS consent       (a live, un-withdrawn grant; read failure refuses)
    3. allowlist         (the dial allowlist, same semantics as ``dialer``)
    4. per-session cap   (a stuck loop exhausts one conversation, not the budget)
    5. hourly budget     (``rate_limits/sms_sends``; unavailable refuses)
    6. quiet hours       (unsolicited sends only, re-checked NOW, review S-4)
    7. provider

    ``compliance_reply`` is the single STOP/HELP answer the carrier rules require
    when we answer keywords ourselves: it skips 1-3 (the person may have just
    opted out, and may not be on any list) but not the cap or the budget.

    Raises :class:`SendBlocked`.
    """
    s = api.cfg.sms
    if not s.enabled:
        raise SendBlocked("SMS follow-up is disabled.")
    prov = provider(api)
    if prov is None:
        raise SendBlocked("SMS provider is not configured.")
    if not is_e164(phone):
        raise SendBlocked("Destination is not E.164.")
    if not compliance_reply:
        try:
            opted = await is_opted_out(api, phone)
        except Exception:
            raise SendBlocked("Opt-out store unavailable.", retryable=True) from None
        if opted:
            raise SendBlocked("Recipient has opted out.")
        try:
            consent = await active_consent(api, phone)
        except Exception:
            raise SendBlocked("Consent store unavailable.", retryable=True) from None
        if consent is None:
            raise SendBlocked("No SMS consent on record for this number.")
        try:
            await check_allowed(api, phone)
        except DialError as exc:
            raise SendBlocked(f"Allowlist: {exc.detail}") from None

    try:
        session = await get_session(api, phone)
    except Exception:
        raise SendBlocked("Session store unavailable.", retryable=True) from None
    if session is not None and int(session.get("message_count") or 0) >= s.max_messages_per_session:
        raise SendBlocked(f"Conversation reached {s.max_messages_per_session} messages.")

    try:
        ok = await budget.consume(api.store, BUDGET_NAME, s.max_sms_per_hour, now=api.now())
    except budget.BudgetUnavailable:
        raise SendBlocked("SMS budget unavailable; refusing to send.", retryable=True) from None
    if not ok:
        raise SendBlocked(f"SMS budget exhausted ({s.max_sms_per_hour}/hour).", retryable=True)

    if unsolicited:
        now_local = local_now(api)
        opens = timeparse.next_send_window(now_local, scheduling(api))
        if opens != now_local:
            await budget.release(api.store, BUDGET_NAME, s.max_sms_per_hour)
            raise SendBlocked("Outside SMS hours.", retryable=True, resume_at=opens)

    from voa_providers.sms import SmsSendError

    try:
        message_sid = await prov.send(phone, body)
    except SmsSendError as exc:
        await budget.release(api.store, BUDGET_NAME, s.max_sms_per_hour)
        if exc.opted_out:
            try:
                await record_opt_out(api, phone, source="carrier:21610", set_session_state=True)
            except Exception:
                log.warning("carrier opt-out not recorded", extra={"event": "sms.optout_failed"})
            raise SendBlocked("Recipient has opted out at the carrier.") from None
        from voa_providers.sms import twilio_reason

        why = twilio_reason(exc.code)
        raise SendBlocked(
            f"Twilio refused the message: {why} (code {exc.code})."
            if why
            else f"Provider refused the message (code {exc.code}).",
            retryable=not exc.permanent,
        ) from None
    except Exception:
        await budget.release(api.store, BUDGET_NAME, s.max_sms_per_hour)
        log.exception("sms send failed", extra={"event": "sms.send_error"})
        raise SendBlocked("Send failed.", retryable=True) from None

    log.info(
        "sms sent",
        extra={"event": "sms.sent", "to": mask_phone(phone), "chars": len(body), "via": prov.name},
    )
    await log_message(
        api,
        phone=phone,
        direction="outbound",
        body=body,
        message_sid=message_sid,
        call_sid=call_id,
    )
    if session is not None:

        def bump(cur):
            if cur is None:
                return None
            return {**cur, "message_count": int(cur.get("message_count") or 0) + 1}

        try:
            await api.store.transact(SESSIONS, phone, bump)
        except Exception:
            log.warning("message count not updated", extra={"event": "sms.count_failed"})
    return message_sid


# --------------------------------------------------------------------------- callbacks


async def cancel_callbacks(api: Api, phone: str, *, status: str, reason: str = "") -> int:
    """Move every still-scheduled callback for a handset to ``status``. The queued
    task cannot be deleted from Cloud Tasks; its handler sees the status and skips."""
    now = _ts(api)
    n = 0
    for callback_id, d in await api.store.query(CALLBACKS, [Where("phone", "==", phone)]):
        if d.get("status") != "scheduled":
            continue

        def mutate(cur):
            if cur is None or cur.get("status") != "scheduled":
                return None
            return {**cur, "status": status, "last_error": reason or None, "updated_at": now}

        done = await api.store.transact(CALLBACKS, callback_id, mutate)
        n += int(bool(done and done.get("status") == status))
    return n


async def _book(api: Api, phone: str, session: dict, when: datetime, base: dict) -> None:
    """Create the booking, queue its dial, then point the session at it."""
    await cancel_callbacks(api, phone, status="superseded", reason="rebooked")
    ctx = session.get("patient_context") or {}
    callback_id = new_id()
    when_utc = when.astimezone(UTC)
    now = _ts(api)
    await api.store.set(
        CALLBACKS,
        callback_id,
        {
            "callback_id": callback_id,
            "phone": phone,
            "run_at": _ts(api, when_utc),
            "status": "scheduled",
            "patient_context": ctx,  # server-side only; never served (S-8)
            "patient_name": ctx.get("patient_name") or session.get("patient_name") or "",
            "patient_id": ctx.get("patient_id") or session.get("patient_id") or "",
            "origin_call_sid": session.get("call_sid"),
            "origin_entry_id": session.get("origin_entry_id"),
            "call_sid": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    try:
        await _enqueue(
            api,
            {"kind": "dispatch_callback", "callback_id": callback_id},
            name=_task_name("sms-callback", callback_id),
            delay_s=(when_utc - api.now()).total_seconds(),
        )
    except Exception:
        await api.store.merge(CALLBACKS, callback_id, {"status": "failed", "updated_at": now})
        raise
    patch = {
        **base,
        "callback_id": callback_id,
        "scheduled_for": _ts(api, when_utc),
        "scheduled_for_text": timeparse.humanize(when, local_now(api)),
        # Outlive the booking: the 48 h TTL is shorter than the booking horizon,
        # and an expired session cannot reason about "C" or a new time.
        "expires_at": _ts(api, when_utc + timedelta(hours=api.cfg.sms.session_ttl_h)),
    }
    try:
        await _patch_required(api, phone, patch)
    except Exception:
        # Never leave a booking the patient was not told about.
        await api.store.merge(CALLBACKS, callback_id, {"status": "canceled", "updated_at": now})
        raise


# --------------------------------------------------------------------------- inbound


async def load_live_session(api: Api, phone: str) -> dict | None:
    """The handset's session, expiring it lazily if its time has passed."""
    session = await get_session(api, phone)
    if session is None:
        return None
    expires = session.get("expires_at") or ""
    if session.get("state") in EXPIRABLE_STATES and expires and expires <= _ts(api):
        await file_abandoned_survey(api, phone, session)
        await patch_session(api, phone, {"state": st.STATE_EXPIRED})
        session["state"] = st.STATE_EXPIRED
    return session


async def handle_inbound(api: Api, phone: str, body: str, message_id: str) -> dict:
    """One patient message (from the gateway's signed webhook, via Cloud Tasks).

    De-duplicated on the provider message id (review C-7): Cloud Tasks delivers at
    least once and the provider redelivers a slow webhook, and replaying "2" in
    ``awaiting_time`` once booked a call nobody asked for. The marker is released
    if processing fails before anything was persisted, so the retry runs; once
    effects are persisted the message is consumed and never re-run.
    """
    await refresh_org(api)
    phone = (phone or "").strip()
    body = (body or "").strip()[:MAX_INBOUND_CHARS]
    if not is_e164(phone):
        return {"ignored": "sender is not E.164"}
    message_id = (message_id or "").strip()
    if message_id:
        try:
            first = await api.store.create(
                INBOUND_SEEN,
                message_id,
                {
                    "seen_at": _ts(api),
                    "expire_at": api.now() + timedelta(days=INBOUND_DEDUPE_DAYS),
                },
            )
        except Exception:
            raise Retryable("inbound de-duplication unavailable") from None
        if not first:
            log.info("duplicate inbound ignored", extra={"event": "sms.inbound_duplicate"})
            return {"duplicate": True}

    progress = {"persisted": False}
    try:
        return await _process_inbound(api, phone, body, message_id, progress)
    except Exception as exc:
        if progress["persisted"]:
            log.exception("inbound failed after persisting", extra={"event": "sms.inbound_error"})
            return {"error": "partially processed"}
        if message_id:
            try:
                await api.store.delete(INBOUND_SEEN, message_id)
            except Exception:
                log.warning("dedupe marker not released", extra={"event": "sms.dedupe_release"})
        if isinstance(exc, Retryable):
            raise
        log.exception("inbound failed; will retry", extra={"event": "sms.inbound_retry"})
        raise Retryable("inbound processing failed") from None


async def _process_inbound(
    api: Api, phone: str, body: str, message_id: str, progress: dict
) -> dict:
    session = await load_live_session(api, phone)
    call_id = (session or {}).get("call_sid")
    # S-8: the typed DOB is never stored in the dashboard thread.
    in_dob_gate = (session or {}).get("state") == st.STATE_AWAITING_DOB
    stored = DOB_REDACTED if in_dob_gate and re.search(r"\d", body) else body
    await log_message(
        api, phone=phone, direction="inbound", body=stored, message_sid=message_id, call_sid=call_id
    )
    now_local = local_now(api)
    decision = await conversation(api).decide(session, body, now_local)

    if decision.effect in (Effect.DISPATCH_RECALL, Effect.BOOK, Effect.REBOOK) and not dialable(
        (session or {}).get("patient_context")
    ):
        # Don't promise a call that cannot be placed.
        decision = Decision(
            reply=messages.cannot_call_back(), state=st.STATE_CLOSED, retry=0, note="undialable"
        )
    log.info(
        "sms inbound decided",
        extra={
            "event": "sms.decision",
            "to": mask_phone(phone),
            "from_state": (session or {}).get("state"),
            "note": decision.note,
            "effect": decision.effect.value,
        },
    )

    emergency = decision.effect in (Effect.FILE_EMERGENCY, Effect.CANCEL_ALL)
    try:
        await apply_decision(api, phone, session, decision)
    except Exception:
        if not emergency:
            raise
        # The 911 copy is never held back by a failed write.
        log.exception("emergency bookkeeping failed", extra={"event": "sms.emergency_error"})
    progress["persisted"] = True

    sent = 0
    compliance_reply = decision.effect in (Effect.OPT_OUT,) or decision.note == "help"
    for text in filter(None, (decision.reply, *decision.extra_replies)):
        try:
            await send_sms(api, phone, text, call_id=call_id, compliance_reply=compliance_reply)
            sent += 1
        except SendBlocked as exc:
            log.warning(
                "reply not sent",
                extra={"event": "sms.reply_blocked", "to": mask_phone(phone), "reason": exc.reason},
            )
            break
    return {"note": decision.note, "effect": decision.effect.value, "replies": sent}


async def apply_decision(api: Api, phone: str, session: dict | None, decision: Decision) -> None:
    """Make the decision durable. Runs BEFORE any reply is sent."""
    base: dict[str, Any] = {"last_inbound_at": _ts(api)}
    if decision.state is not None:
        base["state"] = decision.state
    if decision.retry is not None:
        base["retry_count"] = decision.retry
    if decision.session_patch:
        base.update(decision.session_patch)
    effect = decision.effect

    if effect is Effect.OPT_OUT:
        await record_opt_out(api, phone, source="sms:STOP")
    elif effect is Effect.OPT_IN:
        await record_opt_in(api, phone)
    elif effect is Effect.DISPATCH_RECALL and session is not None:
        # Review S-5: the session says RECALL_DISPATCHED (with this recall's id)
        # BEFORE the task exists, so even a zero-delay task cannot find the old
        # state and drop the call we just promised.
        recall_id = new_id()
        previous = {k: session.get(k) for k in ("state", "retry_count", "recall_id")}
        await _patch_required(api, phone, {**base, "recall_id": recall_id})
        try:
            await _enqueue(
                api,
                {"kind": "dispatch_recall", "phone": phone, "recall_id": recall_id},
                name=_task_name("sms-recall", recall_id),
            )
        except Exception:
            await _patch_quietly(api, phone, previous)
            raise
        return
    elif effect in (Effect.BOOK, Effect.REBOOK) and session is not None and decision.when:
        await _book(api, phone, session, decision.when, base)
        return
    elif effect in (Effect.CANCEL_BOOKING, Effect.CANCEL_ALL):
        await cancel_callbacks(api, phone, status="canceled", reason="patient cancelled")
        base.update({"callback_id": None, "scheduled_for": None, "scheduled_for_text": None})
    elif effect in (Effect.SUBMIT_SURVEY, Effect.FILE_EMERGENCY) and session is not None:
        snap = survey_snapshot(phone, session, decision)
        if snap is not None:
            # Queue first: if anything after this fails, the task files from the
            # snapshot (identity included, review S-9) and the retry of this
            # message collapses onto the same task name.
            await _enqueue(
                api,
                {"kind": "submit_survey", **snap},
                name=_task_name("sms-file", snap["filing_id"]),
                delay_s=SURVEY_SAFETY_NET_DELAY_S,
            )
        await _patch_required(api, phone, base)
        if snap is not None:
            try:
                await submit_survey(api, snap)
            except Exception:
                log.warning("inline filing deferred", extra={"event": "sms.file_deferred"})
        return

    if session is not None:
        await _patch_required(api, phone, base)


# --------------------------------------------------------------------------- the survey


def _filing_id(phone: str, call_id: str | None, kind: str, fallback: str) -> str:
    stem = call_id or ("nocall-" + re.sub(r"\D", "", phone) + "-" + re.sub(r"\D", "", fallback))
    return _task_name(stem, kind)


def survey_snapshot(phone: str, session: dict, decision: Decision) -> dict | None:
    """Everything filing needs, frozen now (S-9). None when there is nothing to file."""
    patch = decision.session_patch or {}
    answers = patch.get("survey_answers")
    if answers is None:
        answers = session.get("survey_answers") or {}
    emergency = decision.effect is Effect.FILE_EMERGENCY
    if not answers and not emergency and not patch.get("survey_outcome"):
        return None
    ctx = session.get("patient_context") or {}
    verified = bool(patch.get("dob_verified") or session.get("dob_verified"))
    call_id = session.get("call_sid")
    # The use case the answers belong to (a session from before campaign ids has
    # none, and files as the default).
    use_case = {}
    if cid := session_campaign_id(session):
        use_case["campaign_id"] = cid
        if session.get("spec_version"):
            use_case["spec_version"] = session["spec_version"]
    return {
        **use_case,
        "phone": phone,
        "call_id": call_id,
        "entry_id": session.get("origin_entry_id"),
        "patient_id": ctx.get("patient_id") or session.get("patient_id") or "",
        "emergency": emergency,
        "answers": answers,
        "outcome": patch.get("survey_outcome"),
        "complete": bool(patch.get("survey_complete")),
        "dob_result": patch.get("dob_verification_result")
        or ("verified" if verified else "skipped"),
        "filing_id": _filing_id(
            phone,
            call_id,
            "emergency" if emergency else "survey",
            str(session.get("created_at") or ""),
        ),
    }


def _snap_campaign(snap: dict):
    return survey.campaign_for(snap.get("campaign_id"))


def _report_payload(snap: dict) -> dict:
    answers = dict(snap.get("answers") or {})
    if snap.get("emergency"):
        outcome: str | None = "emergency_ended"
        follow_up = survey.FOLLOW_UP_EMERGENCY
        reason = "Patient reported a possible emergency by text; advised to call 911."
        reasons = reports.single_reason(follow_up, reason)
    elif snap.get("outcome"):
        outcome = snap["outcome"]
        follow_up = survey.FOLLOW_UP_NONE
        reason = "Could not verify identity by text; no answers collected."
        reasons = []
    else:
        t = survey.triage(answers, _snap_campaign(snap))
        follow_up, reason, reasons = t.follow_up, t.reason, t.reasons
        if t.triggers:
            answers["_triggers"] = list(t.triggers)
        # Never claim questionnaire_completed on a partial: filed with no outcome.
        outcome = "questionnaire_completed" if snap.get("complete") else None
    payload = {
        "patient_id": snap.get("patient_id"),
        "call_sid": snap.get("call_id"),
        "channel": "sms",
        "follow_up": follow_up,
        "follow_up_reason": reason,
        "follow_up_reasons": reasons,
        "survey_responses": answers,
        "dob_verification_result": snap.get("dob_result") or "skipped",
    }
    if outcome:
        payload["outcome"] = outcome
    for key in ("campaign_id", "spec_version"):
        if snap.get(key):
            payload[key] = snap[key]
    return payload


async def _same_call_session(api: Api, snap: dict) -> dict | None:
    """The session only if it still belongs to the call this filing is for."""
    session = await get_session(api, snap["phone"])
    if session and session.get("call_sid") == snap.get("call_id"):
        return session
    return None


async def submit_survey(api: Api, snap: dict) -> dict:
    """File the by-text report onto the origin call, then close with the patient.

    Idempotent end to end (review S-1): the report id is reserved in
    ``sms_filings`` before the report is written, so a retry rewrites the same
    report instead of filing a second one, and the closing is guarded by
    ``closed_at``. The closing goes out only after the report is written: telling
    a patient their care team has their answers and then failing to file them is
    the worst outcome this feature has. A retryable closing failure raises
    :class:`Retryable` (review S-3) instead of being swallowed.
    """
    await refresh_org(api)
    filing_id = snap["filing_id"]
    phone = snap["phone"]
    marker = await api.store.get(FILINGS, filing_id) or {}
    if marker.get("closed_at"):
        return {"skipped": "already filed and closed"}

    follow_up = marker.get("follow_up")
    if not marker.get("report_id"):
        payload = _report_payload(snap)
        clean, errors = reports.validate_report(payload, call_id_pattern=CALL_ID_RE)
        if errors:
            # A schema problem, not a blip: retrying buries it. Tell the patient
            # to use the phone instead. Error texts are static (no patient input),
            # but only the count is logged.
            log.error(
                "by-text report rejected",
                extra={"event": "sms.report_invalid", "errors": len(errors)},
            )
            await api.store.set(
                FILINGS,
                filing_id,
                {"status": "rejected", "errors": errors, "closed_at": _ts(api)},
            )
            if await _same_call_session(api, snap):
                await _patch_quietly(
                    api,
                    phone,
                    {
                        "survey_file_failed": True,
                        "survey_submitted_at": _ts(api),
                        "state": st.STATE_CLOSED,
                    },
                )
            try:
                await send_sms(
                    api,
                    phone,
                    messages.survey_file_failed(_snap_campaign(snap)),
                    call_id=snap["call_id"],
                )
            except SendBlocked:
                log.warning("filing-failed notice not sent", extra={"event": "sms.notice_blocked"})
            return {"error": "report rejected"}

        proposed = reports.report_doc_id(clean["patient_id"], api.now())

        def reserve(cur):
            if cur and cur.get("pending_report_id"):
                return None
            return {**(cur or {}), "pending_report_id": proposed, "created_at": _ts(api)}

        reserved = await api.store.transact(FILINGS, filing_id, reserve) or {}
        report_id = reserved.get("pending_report_id") or proposed
        call_id = snap.get("call_id")
        await api.store.set(
            REPORTS,
            report_id,
            {**clean, "call_id": call_id, "created_at": _ts(api), "source": "sms"},
        )
        patch, settles = reports.report_call_patch(clean, report_id)
        if call_id:
            now = api.now()

            def merge(cur):
                if cur is None:
                    return None  # never invent a call doc
                return lifecycle.merge_call_doc(cur, patch, now=now, from_report=True)

            call = await api.store.transact(CALLS, call_id, merge)
            reopened = (call or {}).get("review") or {}
            if reopened.get("export_withdrawn") and reopened.get("reopened_at") == lifecycle.iso(
                now
            ):
                # This report reopened a signed-off call: its EHR record is void (B-5).
                await api.store.merge(
                    EHR_OUTBOX,
                    call_id,
                    review.withdrawn_export(
                        lifecycle.iso(now), "a later report arrived for this call"
                    ),
                )
            entry_id = snap.get("entry_id") or (call or {}).get("entry_id")
            if settles and entry_id and call and call.get("call_status"):
                status = call["call_status"]

                def settle(entry):
                    if entry is None:
                        return None
                    p = compliance.reconcile_entry_patch(entry, status)
                    return {**entry, **p, "updated_at": _ts(api)} if p else None

                # The report settled the call; settle its call-list entry too, or
                # the next run would ring a patient who already answered by text.
                await api.store.transact(CALL_LIST, entry_id, settle)
            if call:
                # Shadow mode (docs/KNOWLEDGE-PLAN.md): what the knowledge agent
                # would have said to each question, drafted in the background.
                from voa_core import knowledge

                await knowledge.enqueue_shadow(
                    api.store,
                    api.tasks,
                    api.cfg.services.queue_events,
                    call_id=call_id,
                    survey=clean.get("survey_responses"),
                    channel="sms",
                    report_id=report_id,
                )
        follow_up = clean["follow_up"]
        await api.store.merge(
            FILINGS,
            filing_id,
            {"report_id": report_id, "follow_up": follow_up, "filed_at": _ts(api)},
        )
        if await _same_call_session(api, snap):
            await _patch_quietly(
                api, phone, {"survey_submitted_at": _ts(api), "report_id": report_id}
            )
        log.info(
            "by-text report filed",
            extra={"event": "sms.report_filed", "follow_up": follow_up, "settles": settles},
        )

    if not snap.get("complete") or snap.get("emergency"):
        # A partial or an emergency has already had its closing word said.
        await api.store.merge(FILINGS, filing_id, {"closed_at": _ts(api)})
        session = await _same_call_session(api, snap)
        if session and session.get("state") in st.SURVEY_STATES:
            await _patch_quietly(
                api, phone, {"survey_closed_at": _ts(api), "state": st.STATE_CLOSED}
            )
        return {"filed": True}

    # Every completed closing carries the spec's closing_extra (post-op: the
    # medication-list reminder); one that recorded questions also says the care
    # team will follow up on them.
    asked = asked_questions(snap.get("answers") or {})
    spec = _snap_campaign(snap)
    closing = (
        messages.survey_done_urgent(asked_questions=asked, campaign=spec)
        if follow_up in (survey.FOLLOW_UP_URGENT, survey.FOLLOW_UP_EMERGENCY)
        else messages.survey_done_routine(asked_questions=asked, campaign=spec)
    )
    try:
        await send_sms(api, phone, closing, call_id=snap.get("call_id"))
    except SendBlocked as exc:
        if exc.retryable:
            raise Retryable(f"closing not sent: {exc.reason}") from None
        log.warning(
            "closing permanently blocked",
            extra={"event": "sms.closing_blocked", "reason": exc.reason},
        )
    await api.store.merge(FILINGS, filing_id, {"closed_at": _ts(api)})
    if await _same_call_session(api, snap):
        await _patch_quietly(api, phone, {"survey_closed_at": _ts(api), "state": st.STATE_CLOSED})
    return {"filed": True, "closed": True}


async def file_abandoned_survey(api: Api, phone: str, session: dict) -> bool:
    """A lapsed questionnaire that already carries an urgent finding is filed
    (with no outcome) rather than dropped. Anything else lapses silently."""
    await refresh_org(api)
    if session.get("state") not in st.SURVEY_STATES or session.get("survey_submitted_at"):
        return False
    answers = session.get("survey_answers") or {}
    spec = campaign_of(session)
    if spec is None or not survey.has_urgent_finding(answers, spec):
        return False
    decision = Decision(
        effect=Effect.SUBMIT_SURVEY,
        session_patch={"survey_answers": answers, "survey_complete": False},
    )
    snap = survey_snapshot(phone, session, decision)
    if snap is None:
        return False
    await _enqueue(
        api,
        {"kind": "submit_survey", **snap},
        name=_task_name("sms-file", snap["filing_id"]),
        delay_s=SURVEY_SAFETY_NET_DELAY_S,
    )
    try:
        await submit_survey(api, snap)
    except Exception:
        log.warning("abandoned filing deferred", extra={"event": "sms.file_deferred"})
    log.info("abandoned survey filed", extra={"event": "sms.abandoned_filed"})
    return True


# --------------------------------------------------------------------------- invites


async def _patient_context(api: Api, call: dict) -> dict:
    """The context a follow-up needs. The call doc never holds the DOB (C-2), so
    it comes from the call-list entry that produced the call, when there is one."""
    ctx = {k: v for k, v in (call.get("patient_context") or {}).items() if k != "dob"}
    ctx.setdefault("patient_name", call.get("patient_name") or "")
    ctx.setdefault("patient_id", call.get("patient_id") or "")
    entry_id = call.get("entry_id")
    if entry_id:
        entry = await api.store.get(CALL_LIST, entry_id) or {}
        if entry.get("phone") == call.get("to"):
            if entry.get("patient_dob"):
                ctx["patient_dob"] = entry["patient_dob"]
            ctx.setdefault("is_diabetic", entry.get("is_diabetic", False))
            if entry.get("care_setting"):
                ctx.setdefault("care_setting", entry["care_setting"])
    # The call's use case: the questionnaire, a booked callback and a recall dial
    # all run on it (patient_context travels with each of them).
    if call.get("campaign_id"):
        ctx.setdefault("campaign_id", call["campaign_id"])
    return ctx


async def handle_missed_call(api: Api, call_id: str) -> dict:
    """``/tasks/call-terminal`` saw rna/busy/voicemail: queue the invite.

    Delayed by ``invite_delay_s`` and pushed into the send window; named by
    handset + call so a duplicate delivery collapses onto one task. Whether to
    actually text is decided when the invite task runs (cooldown, consent,
    quiet hours again, a survey in progress, a booking already made).
    """
    if not api.cfg.sms.enabled:
        return {"skipped": "sms disabled"}
    call = await api.store.get(CALLS, call_id)
    if not call:
        return {"skipped": "no call"}
    if call.get("call_status") not in UNANSWERED:
        return {"skipped": f"call is {call.get('call_status')}"}
    phone = (call.get("to") or "").strip()
    if not is_e164(phone):
        return {"skipped": "no textable number"}
    now_local = local_now(api)
    run_at = timeparse.next_send_window(
        now_local + timedelta(seconds=api.cfg.sms.invite_delay_s), scheduling(api)
    )
    delay = (run_at - now_local).total_seconds()
    queued = await _enqueue(
        api,
        {"kind": "send_invite", "phone": phone, "call_id": call_id},
        name=_task_name("sms-invite", re.sub(r"\D", "", phone), call_id),
        delay_s=delay,
    )
    return {"queued": queued, "delay_s": delay}


async def _defer_invite(api: Api, payload: dict, opens: datetime) -> dict:
    delay = (opens - local_now(api)).total_seconds()
    await _enqueue(
        api,
        {"kind": "send_invite", "phone": payload["phone"], "call_id": payload["call_id"]},
        name=_task_name(
            "sms-invite",
            re.sub(r"\D", "", payload["phone"]),
            payload["call_id"],
            opens.strftime("%Y%m%d%H%M"),
        ),
        delay_s=delay,
    )
    log.info("invite deferred to the send window", extra={"event": "sms.invite_deferred"})
    return {"deferred": _ts(api, opens)}


async def send_invite(api: Api, payload: dict) -> dict:
    await refresh_org(api)
    phone, call_id = payload.get("phone") or "", payload.get("call_id") or ""
    if not api.cfg.sms.enabled:
        return {"skipped": "sms disabled"}
    call = await api.store.get(CALLS, call_id) if call_id else None
    if not call:
        return {"skipped": "no call"}
    # A late report can promote rna -> success while this task waited.
    if call.get("call_status") not in UNANSWERED:
        return {"skipped": "call no longer unanswered"}
    if call.get("to") != phone or not is_e164(phone):
        return {"skipped": "number mismatch"}
    try:
        if await is_opted_out(api, phone):
            await _mark_follow_up(api, call_id, "not_sent", _skip_text(api, "opted out"))
            return {"skipped": "opted out"}
    except Exception:
        raise Retryable("opt-out store unavailable") from None

    # Quiet hours, re-checked when the task runs (review S-4), not only when queued.
    now_local = local_now(api)
    opens = timeparse.next_send_window(now_local, scheduling(api))
    if opens != now_local:
        return await _defer_invite(api, payload, opens)

    ctx = await _patient_context(api, call)
    settings = await get_settings(api)
    cooldown_on = settings["invite_cooldown_enabled"]
    if not cooldown_on:
        log.warning(
            "invite cooldown is DISABLED",
            extra={"event": "sms.cooldown_off", "to": mask_phone(phone)},
        )
    now = _ts(api)
    cutoff = _ts(api, api.now() - timedelta(hours=api.cfg.sms.invite_cooldown_h))
    seen: dict[str, Any] = {}

    def start(cur):
        seen.clear()
        if cur:
            # A new invite RESETS the session: mid-survey that would wipe answers.
            if cur.get("state") in st.SURVEY_STATES and not cur.get("survey_submitted_at"):
                seen["skip"] = "survey in progress"
                return None
            last = cur.get("last_invite_at") or ""
            if cooldown_on and last and last >= cutoff:
                seen["skip"] = "invite cooldown"
                return None
            # They already chose a time: a fresh menu would silently supersede it.
            if cur.get("callback_id") and (cur.get("scheduled_for") or "") > now:
                seen["skip"] = "callback already booked"
                return None
        seen["previous_invite_at"] = (cur or {}).get("last_invite_at")
        return {
            "phone": phone,
            "call_sid": call_id,
            "origin_entry_id": call.get("entry_id"),
            "patient_context": ctx,
            "patient_name": ctx.get("patient_name") or "",
            "patient_id": ctx.get("patient_id") or "",
            **(
                {"campaign_id": ctx["campaign_id"], "spec_version": call.get("spec_version")}
                if ctx.get("campaign_id")
                else {}
            ),
            "state": st.STATE_AWAITING_CHOICE,
            "retry_count": 0,
            "message_count": 0,  # per conversation, not per handset
            "created_at": (cur or {}).get("created_at") or now,
            "updated_at": now,
            # Stamped BEFORE sending and rolled back on an observed failure: a
            # crash after the provider accepted costs one missed invite, never a
            # duplicate.
            "last_invite_at": now,
            "last_inbound_at": None,
            "expires_at": _ts(api, api.now() + timedelta(hours=api.cfg.sms.session_ttl_h)),
            "callback_id": None,
            "scheduled_for": None,
            "scheduled_for_text": None,
            "last_error": None,
        }

    # Transactional, so two invite tasks for the same handset cannot both pass the
    # cooldown (the old check-then-act could).
    await api.store.transact(SESSIONS, phone, start)
    if "skip" in seen:
        log.info(
            "invite skipped",
            extra={"event": "sms.invite_skipped", "reason": seen["skip"], "to": mask_phone(phone)},
        )
        # Say why on the call: a skipped text used to leave no trace a nurse could see.
        await _mark_follow_up(api, call_id, "not_sent", _skip_text(api, seen["skip"]))
        return {"skipped": seen["skip"]}
    # A previous conversation's booking must not dial after a fresh menu.
    await cancel_callbacks(api, phone, status="superseded", reason="new follow-up started")

    try:
        await send_sms(
            api,
            phone,
            messages.invite(ctx.get("patient_name"), site_copy(api)),
            call_id=call_id,
            unsolicited=True,
        )
    except SendBlocked as exc:
        await _patch_quietly(
            api,
            phone,
            {
                "state": st.STATE_CLOSED,
                "last_error": exc.reason[:300],
                "last_invite_at": seen.get("previous_invite_at"),
            },
        )
        log.warning(
            "invite not sent",
            extra={"event": "sms.invite_blocked", "reason": exc.reason, "to": mask_phone(phone)},
        )
        if exc.resume_at is not None:
            await _mark_follow_up(api, call_id, "deferred", exc.reason)
            return await _defer_invite(api, payload, exc.resume_at)
        if exc.retryable:
            raise Retryable(exc.reason) from None
        await _mark_follow_up(api, call_id, "not_sent", exc.reason)
        return {"blocked": exc.reason}
    await _mark_follow_up(api, call_id, "sent")
    return {"sent": True}


def _skip_text(api: Api, why: str) -> str:
    """Why a missed call got no text, in words a nurse can act on."""
    return {
        "invite cooldown": "this number already got a follow-up text in the last "
        f"{api.cfg.sms.invite_cooldown_h} hours (the limit can be turned off in Settings)",
        "survey in progress": "the patient is in the middle of answering the questions by text",
        "callback already booked": "the patient already booked a callback by text",
        "opted out": "the patient replied STOP to texts",
    }.get(why, why)


async def _mark_follow_up(api: Api, call_id: str, status: str, reason: str = "") -> None:
    """Say on the call whether its follow-up text went out, and if not why: a
    nurse looking at a voicemail otherwise cannot tell a skipped text (no consent,
    the other system has the number, ...) from one that is on its way."""
    try:
        await api.store.merge(
            CALLS,
            call_id,
            {"sms_follow_up": {"status": status, "reason": reason[:300], "at": _ts(api)}},
        )
    except Exception:
        log.warning("could not stamp sms follow-up", extra={"event": "sms.follow_up_stamp_failed"})


# --------------------------------------------------------------------------- dialling


async def _place(api: Api, phone: str, ctx: dict, *, placed_via: str, entry_id: str | None) -> str:
    """Through ``dialer.dial`` -- the only way a call is placed -- so the
    allowlist, the dial budget and the call doc all apply as for any other call."""
    if ctx.get(SIMULATOR_FLAG):
        # The dry-run SMS simulator's fictional patient: "call me" / a booked
        # time is played through, but no phone ever rings for it.
        raise DialError(403, "SMS simulator test patient: no call is placed.")
    try:
        req = PlaceCallRequest.model_validate(
            {
                "phone": phone,
                "patient_name": ctx.get("patient_name") or "",
                "patient_id": ctx.get("patient_id") or "",
                "patient_dob": ctx.get("patient_dob") or "",
                "operation_name": ctx.get("operation_name") or None,
                "operation_date": ctx.get("operation_date") or None,
                "is_diabetic": ctx.get("is_diabetic", False),
                "care_setting": ctx.get("care_setting") or None,
                "campaign_id": ctx.get("campaign_id") or None,
            }
        )
    except ValidationError:
        raise DialError(400, "Patient context is incomplete; cannot place the call.") from None
    return await dial(api, req, placed_via=placed_via, entry_id=entry_id)


async def dispatch_recall(api: Api, payload: dict) -> dict:
    """ "Call me now"."""
    phone, recall_id = payload.get("phone") or "", payload.get("recall_id") or ""
    session = await get_session(api, phone)
    if (
        not session
        or session.get("state") != st.STATE_RECALL_DISPATCHED
        or session.get("recall_id") != recall_id
    ):
        return {"skipped": "recall no longer wanted"}
    try:
        opted = await is_opted_out(api, phone)
    except Exception:
        raise Retryable("opt-out store unavailable") from None
    if opted:
        await _patch_quietly(api, phone, {"state": st.STATE_OPTED_OUT})
        return {"skipped": "opted out"}

    won: dict[str, bool] = {}

    def claim(cur):
        # Decided inside the transaction: the returned doc alone cannot tell "I
        # moved it to dialing" from "someone else already had".
        won["ok"] = bool(
            cur
            and cur.get("state") == st.STATE_RECALL_DISPATCHED
            and cur.get("recall_id") == recall_id
        )
        return {**cur, "state": st.STATE_DIALING, "updated_at": _ts(api)} if won["ok"] else None

    claimed = await api.store.transact(SESSIONS, phone, claim)
    if not won.get("ok") or not claimed:
        return {"skipped": "already claimed"}

    try:
        call_id = await _place(
            api,
            phone,
            claimed.get("patient_context") or {},
            placed_via="sms_recall",
            entry_id=claimed.get("origin_entry_id"),
        )
    except DialError as exc:
        if not exc.permanent:
            await _patch_quietly(
                api,
                phone,
                {"state": st.STATE_RECALL_DISPATCHED, "last_error": exc.detail[:300]},
            )
            raise Retryable(exc.detail) from None
        await _patch_quietly(api, phone, {"state": st.STATE_CLOSED, "last_error": exc.detail[:300]})
        # They were told "we'll call you shortly": say it is not going to happen.
        try:
            await send_sms(api, phone, messages.cannot_call_back(), call_id=claimed.get("call_sid"))
        except SendBlocked:
            pass
        log.error("recall refused", extra={"event": "sms.recall_refused", "status": exc.status})
        return {"error": exc.detail}

    # The phone is ringing: nothing below may raise into a retry.
    await _patch_quietly(
        api,
        phone,
        {"state": st.STATE_CLOSED, "recall_call_sid": call_id, "last_error": None},
    )
    return {"call_id": call_id}


async def dispatch_callback(api: Api, payload: dict) -> dict:
    """A booked callback whose time has come (a delayed task)."""
    callback_id = payload.get("callback_id") or ""
    callback = await api.store.get(CALLBACKS, callback_id) if callback_id else None
    if callback is None:
        return {"skipped": "no callback"}
    if callback.get("status") != "scheduled":
        # canceled, superseded, dispatched and -- crucially -- dialing.
        return {"skipped": f"callback is {callback.get('status')}"}
    phone = callback["phone"]
    try:
        opted = await is_opted_out(api, phone)
    except Exception:
        raise Retryable("opt-out store unavailable") from None
    if opted:
        await api.store.merge(
            CALLBACKS,
            callback_id,
            {"status": "canceled", "last_error": "recipient opted out", "updated_at": _ts(api)},
        )
        return {"skipped": "opted out"}

    won: dict[str, bool] = {}

    def claim(cur):
        won["ok"] = bool(cur and cur.get("status") == "scheduled")
        return {**cur, "status": "dialing", "updated_at": _ts(api)} if won["ok"] else None

    claimed = await api.store.transact(CALLBACKS, callback_id, claim)
    if not won.get("ok") or not claimed:
        return {"skipped": "already claimed"}
    try:
        call_id = await _place(
            api,
            phone,
            claimed.get("patient_context") or {},
            placed_via="sms_callback",
            entry_id=claimed.get("origin_entry_id"),
        )
    except DialError as exc:
        status = "failed" if exc.permanent else "scheduled"
        await api.store.merge(
            CALLBACKS,
            callback_id,
            {"status": status, "last_error": exc.detail[:300], "updated_at": _ts(api)},
        )
        if not exc.permanent:
            raise Retryable(exc.detail) from None
        log.error("callback refused", extra={"event": "sms.callback_refused", "status": exc.status})
        return {"error": exc.detail}

    try:
        await api.store.merge(
            CALLBACKS,
            callback_id,
            {"status": "dispatched", "call_sid": call_id, "updated_at": _ts(api)},
        )
        session = await get_session(api, phone)
        if session and session.get("callback_id") == callback_id:
            await patch_session(api, phone, {"state": st.STATE_CLOSED, "recall_call_sid": call_id})
    except Exception:
        log.exception("callback placed but bookkeeping failed", extra={"event": "sms.cb_books"})
    return {"call_id": call_id}


async def cancel_callback(api: Api, callback_id: str, *, by: str) -> dict | None:
    """Dashboard cancel. None when there is no such callback; raises ValueError
    with the current status when it is no longer cancellable."""
    callback = await api.store.get(CALLBACKS, callback_id)
    if callback is None:
        return None

    won: dict[str, bool] = {}

    def mutate(cur):
        won["ok"] = bool(cur and cur.get("status") == "scheduled")
        if not won["ok"]:
            return None
        return {**cur, "status": "canceled", "canceled_by": by, "updated_at": _ts(api)}

    done = await api.store.transact(CALLBACKS, callback_id, mutate)
    if not won.get("ok"):
        raise ValueError((done or callback).get("status") or "unknown")
    phone = callback["phone"]
    session = await get_session(api, phone)
    if session and session.get("callback_id") == callback_id:
        await patch_session(
            api,
            phone,
            {
                "state": st.STATE_CLOSED,
                "callback_id": None,
                "scheduled_for": None,
                "scheduled_for_text": None,
            },
        )
    return {"status": "canceled", "callback_id": callback_id}


# --------------------------------------------------------------------------- expiry


async def expire_sessions(api: Api, *, limit: int = 500) -> dict:
    """Expire live sessions past ``expires_at`` (Cloud Scheduler).

    Terminal sessions carry :data:`NEVER`, so this range query only returns
    sessions that need work, however many conversations have ever existed
    (review C-13). A legacy terminal row that slips in is healed to NEVER.
    """
    now = _ts(api)
    rows = await api.store.query(
        SESSIONS, [Where("expires_at", "<=", now)], order_by="expires_at", limit=limit
    )
    expired = healed = filed = 0
    for phone, doc in rows:
        session = {"phone": phone, **doc}
        if session.get("state") in EXPIRABLE_STATES:
            if await file_abandoned_survey(api, phone, session):
                filed += 1
            await patch_session(api, phone, {"state": st.STATE_EXPIRED})
            expired += 1
        else:
            await patch_session(api, phone, {"expires_at": NEVER})
            healed += 1
    return {"expired": expired, "filed": filed, "healed": healed, "scanned": len(rows)}


# --------------------------------------------------------------------------- dashboard views

SESSION_FIELDS = (
    "phone",
    "state",
    "patient_name",
    "patient_id",
    "call_sid",
    "callback_id",
    "scheduled_for",
    "scheduled_for_text",
    "message_count",
    "last_invite_at",
    "last_inbound_at",
    "updated_at",
    "created_at",
    "last_error",
)
CALLBACK_FIELDS = (
    "callback_id",
    "phone",
    "run_at",
    "status",
    "patient_name",
    "patient_id",
    "origin_call_sid",
    "call_sid",
    "last_error",
)


def session_view(phone: str, doc: dict) -> dict:
    """Whitelisted: never the DOB, the patient context or the answers (S-8)."""
    out = {k: doc.get(k) for k in SESSION_FIELDS if k in doc}
    out["phone"] = phone
    return out


def callback_view(callback_id: str, doc: dict) -> dict:
    out = {k: doc.get(k) for k in CALLBACK_FIELDS if k in doc}
    out["callback_id"] = callback_id
    return out


ACTIONS = {
    "send_invite": send_invite,
    "dispatch_recall": dispatch_recall,
    "dispatch_callback": dispatch_callback,
    "submit_survey": submit_survey,
}


async def run_action(api: Api, payload: dict) -> dict:
    """``/tasks/sms-action``. Raises :class:`Retryable` for a 503."""
    kind = str(payload.get("kind") or "")
    handler = ACTIONS.get(kind)
    if handler is None:
        return {"error": "unknown action kind"}  # 2xx: retrying cannot fix it
    return await handler(api, {k: v for k, v in payload.items() if k != "kind"})
