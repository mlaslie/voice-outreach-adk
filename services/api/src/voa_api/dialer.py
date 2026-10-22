"""The one place outbound calls are placed (review: "single choke point").

Order: validate -> allowlist (fail closed) -> budget (reserve before dialing) ->
provider -> record call + stage patient context (read-once by the gateway).
"""

from __future__ import annotations

import logging
from typing import Any

from voa_core import budget, compliance, languages, lifecycle
from voa_core.campaign import DEFAULT_CAMPAIGN_ID, load_campaign
from voa_core.logging import mask_phone
from voa_core.store import now_iso
from voa_providers.base import AmdOptions, PlaceCallRequest, ProviderRejected

from .context import ALLOWLIST, CALLS, PENDING, Api

log = logging.getLogger(__name__)

# Provider error codes that will fail the same way every time (bad/unroutable number).
NON_RETRYABLE_PROVIDER_CODES = {21211, 21214, 21217, 21612, 21614, 13224}


class DialError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail

    @property
    def permanent(self) -> bool:
        return self.status in (400, 403)


class OutsideCallingHours(DialError):
    """A scheduled or automatic dial outside the site's calling hours (old proxy
    B-8). Not permanent: the patient waits for the next opening."""

    def __init__(self, detail: str):
        super().__init__(409, detail)


class LanguageHeld(DialError):
    """The patient's language has no approved language pack for this use case and
    no admin chose "Call in English" (docs/LANGUAGES-PLAN.md §3.2). Never called
    in English silently. Permanent for the call being attempted: retrying the
    same request cannot succeed until the entry or the packs change."""

    def __init__(self, detail: str):
        super().__init__(409, detail)

    @property
    def permanent(self) -> bool:
        return True


def check_language(req: Any, campaign_id: str | None) -> None:
    """Refuse a held language (raises LanguageHeld)."""
    language = getattr(req, "language", None)
    if languages.hold_reason(
        language, campaign_id, compliance.coerce_bool(getattr(req, "call_in_english", False))
    ):
        raise LanguageHeld(languages.held_message(getattr(req, "patient_name", ""), language))


def check_calling_hours(api: Api, hours: compliance.RecallSettings | None) -> None:
    """Refuse to dial outside ``hours`` in the site's timezone. ``None`` (a nurse's
    own call or run) is not limited."""
    if hours is None:
        return
    tz = api.cfg.site.timezone
    if not compliance.in_calling_window(api.now(), hours, tz):
        fmt = api.cfg.site.nurse_time_format  # sync path: the configured default
        raise OutsideCallingHours(
            f"Outside calling hours ({compliance.window_text(hours, tz, fmt)})."
        )


async def runtime_allowlist(api: Api) -> list[str] | None:
    try:
        return [doc_id for doc_id, _ in await api.store.query(ALLOWLIST)]
    except Exception:
        log.exception("allowlist read failed", extra={"event": "allowlist.read_failed"})
        return None  # fail closed: only the static list applies


async def check_allowed(api: Api, phone: str) -> None:
    try:
        static = compliance.parse_allowlist(api.cfg.dialing.allowlist)
    except compliance.AllowlistConfigError as exc:
        raise DialError(503, f"Outbound allowlist misconfigured: {exc}") from None
    decision = compliance.check_destination(phone, static, await runtime_allowlist(api))
    if decision is compliance.DialDecision.NOT_CONFIGURED:
        raise DialError(503, "No outbound allowlist is configured; refusing to dial.")
    if decision is compliance.DialDecision.BLOCKED:
        raise DialError(403, "Number is not on the outbound allowlist.")


def patient_from(req: Any) -> dict[str, Any]:
    first, _, last = req.patient_name.partition(" ")
    return {
        "first_name": first,
        "last_name": last,
        "patient_name": req.patient_name,
        "patient_id": req.patient_id,
        "dob": req.patient_dob,
        "operation_name": req.operation_name,
        "operation_date": req.operation_date,
        "is_diabetic": req.is_diabetic,
        "care_setting": getattr(req, "care_setting", None),
        # Recorded only in step 1: the gateway strips both before the session, so
        # the call itself is unchanged (docs/LANGUAGES-PLAN.md).
        "language": getattr(req, "language", None) or languages.DEFAULT,
        "call_in_english": bool(getattr(req, "call_in_english", False)),
    }


async def dial(
    api: Api,
    req: Any,
    *,
    placed_via: str,
    entry_id: str | None = None,
    run_id: str | None = None,
    attempt: int | None = None,
    hours: compliance.RecallSettings | None = None,
    campaign_id: str | None = None,
) -> str:
    """Place one call. Returns the call id. Raises DialError.

    ``hours``: the calling hours a scheduled or automatic dial must respect,
    checked here at dial time (not only when the run was created). ``attempt``:
    the call-list attempt this call belongs to, so a late result for an older
    attempt never resets the row a newer one is calling on.

    ``campaign_id``: the use case, else the request's own ``campaign_id``, else
    post-op. The staging doc and the call doc record it with the spec's version,
    which is what the gateway and the SMS follow-up then run."""
    check_calling_hours(api, hours)
    campaign_id = campaign_id or getattr(req, "campaign_id", None) or DEFAULT_CAMPAIGN_ID
    try:
        spec_version = load_campaign(campaign_id).version
    except (OSError, ValueError):
        raise DialError(400, f"Unknown use case {campaign_id!r}.") from None
    check_language(req, campaign_id)
    await check_allowed(api, req.phone)
    if api.voice is None:
        raise DialError(503, "Telephony is not configured (provider secrets missing).")
    limit = api.cfg.dialing.max_dials_per_hour
    try:
        if not await budget.consume(api.store, "outbound_dials", limit):
            raise DialError(429, "Hourly dial budget exhausted.")
    except budget.BudgetUnavailable:
        raise DialError(503, "Dial budget unavailable; refusing to dial.") from None

    base = api.cfg.services.gateway_url.rstrip("/")
    amd = api.cfg.amd
    try:
        placed = await api.voice.place_call(
            PlaceCallRequest(
                to=req.phone,
                answer_url=f"{base}/twilio/answer",
                status_url=f"{base}/twilio/status",
                amd=AmdOptions(amd.mode, amd.timeout_s, amd.speech_end_threshold_ms)
                if amd.enabled
                else None,
            )
        )
    except ProviderRejected as exc:
        await budget.release(api.store, "outbound_dials", limit)
        code = int(exc.code) if str(exc.code).isdigit() else None
        status = 400 if code in NON_RETRYABLE_PROVIDER_CODES else 502
        log.warning(
            "provider rejected call",
            extra={"event": "dial.rejected", "code": exc.code, "to": mask_phone(req.phone)},
        )
        raise DialError(status, f"Provider rejected the call (code {exc.code}).") from None
    except Exception:
        await budget.release(api.store, "outbound_dials", limit)
        log.exception("dial failed", extra={"event": "dial.error"})
        raise DialError(502, "Could not reach the telephony provider.") from None

    patient = patient_from(req)
    # approved | draft_pack | machine (the demo auto-approve): which wording this
    # call is spoken with, so reviewers and analytics can find unreviewed calls.
    # Absent for a call spoken in English.
    source = languages.call_wording_source(
        patient["language"], campaign_id, patient["call_in_english"]
    )
    # PHI (DOB) goes only to the read-once staging doc; the call doc never holds it (C-2).
    await api.store.set(
        PENDING,
        placed.call_id,
        {**patient, "campaign_id": campaign_id, "spec_version": spec_version},
    )
    now = now_iso(api.now())
    await api.store.set(
        CALLS,
        placed.call_id,
        {
            "call_sid": placed.call_id,
            "to": req.phone,
            "from": api.cfg.providers.get("twilio", {}).get("from_number", ""),
            "initiated_at": now,
            "updated_at": now,
            "patient_id": req.patient_id,
            "patient_name": req.patient_name,
            "patient_context": lifecycle.strip_phi_for_call_doc(
                {k: v for k, v in patient.items() if k != "dob"}
            ),
            "placed_via": placed_via,
            "amd_requested": amd.enabled,
            "campaign_id": campaign_id,
            "spec_version": spec_version,
            "language": patient["language"],
            "call_in_english": patient["call_in_english"],
            **({"language_source": source} if source else {}),
            "entry_id": entry_id,
            "entry_attempt": attempt,
            "run_id": run_id,
            "last_provider_status": placed.status,
        },
    )
    log.info(
        "call placed",
        extra={
            "event": "dial.placed",
            "call_id": placed.call_id,
            "via": placed_via,
            "to": mask_phone(req.phone),
        },
    )
    return placed.call_id
