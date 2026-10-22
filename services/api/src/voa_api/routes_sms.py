"""SMS follow-up routes.

Dashboard (UI service account, like every other ``/api`` route; shapes match
``ui/src/lib/followups.ts``):

  GET    /api/sms/sessions                      {sessions: FollowUpSession[]}
  GET    /api/sms/callbacks                     {callbacks: ScheduledCallback[]}
  DELETE /api/sms/callbacks/{callback_id}       {status, callback_id}
  GET    /api/sms/messages?phone=&call_sid=     {phone, call_sid, messages: SmsMessage[]}
  GET    /api/sms/consent?phone=                {phone, granted, consent, history}
  POST   /api/sms/consent                       {phone, granted}
  GET    /api/sms/settings                      {settings, defaults}
  POST   /api/sms/settings                      {settings}
  POST   /api/sms/test/start   {phone, diabetic?}  dry-run simulator (sms_simulator.py)
  POST   /api/sms/test/reply   {phone, body}       409 unless sms.dry_run

Cloud Tasks / Scheduler (tasks-invoker only; 503 = retry, 2xx = done):

  POST /tasks/sms-missed-call   {call_id}
  POST /tasks/sms-inbound       {from, body, message_id}
  POST /tasks/sms-action        {kind: send_invite|dispatch_recall|dispatch_callback|
                                 submit_survey, ...}
  POST /cron/sms-expire

No route returns ``patient_dob``, the patient context or survey answers: views
are whitelisted in ``sms_service``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from voa_core.logging import mask_phone

from . import sms_service as sms
from . import sms_simulator
from .auth import acting_user, require_caller, task_callers
from .context import Api
from .models import PlaceCallRequest
from .routes_calls import api_of

log = logging.getLogger(__name__)


def _tasks_api(request: Request) -> Api:
    require_caller(request, task_callers(request))
    return request.app.state.api


dashboard = APIRouter(prefix="/api/sms", dependencies=[Depends(api_of)])
tasks = APIRouter(dependencies=[Depends(_tasks_api)])
router = APIRouter()


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ConsentInput(_In):
    phone: str
    granted: bool = True
    statement_version: str = Field(default="", max_length=100)
    granted_by: str = Field(default="", max_length=200)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return PlaceCallRequest._phone(v)


class SettingInput(_In):
    name: str
    value: bool


class SimStartInput(_In):
    phone: str = Field(max_length=20)
    diabetic: bool = False
    #: One of the 24 codes (default English). Another language uses its pack's
    #: SMS wording, a draft included: the simulator sends nothing.
    language: str | None = Field(default=None, max_length=20)


class SimReplyInput(_In):
    phone: str = Field(max_length=20)
    body: str = Field(max_length=sms.MAX_INBOUND_CHARS)


def _phone_or_400(phone: str) -> str:
    if not sms.is_e164(phone):
        raise HTTPException(400, "phone must be E.164.")
    return phone


def _retry(exc: sms.Retryable) -> JSONResponse:
    return JSONResponse({"retry": str(exc)}, status_code=503)


# --------------------------------------------------------------------------- dashboard


@dashboard.get("/sessions")
async def list_sessions(request: Request, limit: int = 200):
    api: Api = request.app.state.api
    rows = await api.store.query(
        sms.SESSIONS, order_by="updated_at", descending=True, limit=max(1, min(limit, 500))
    )
    return {"sessions": [sms.session_view(phone, d) for phone, d in rows]}


@dashboard.get("/callbacks")
async def list_callbacks(request: Request, limit: int = 200):
    api: Api = request.app.state.api
    rows = await api.store.query(sms.CALLBACKS, order_by="run_at", limit=max(1, min(limit, 500)))
    return {"callbacks": [sms.callback_view(i, d) for i, d in rows]}


@dashboard.delete("/callbacks/{callback_id}")
async def cancel_callback(callback_id: str, request: Request):
    api: Api = request.app.state.api
    try:
        result = await sms.cancel_callback(api, callback_id, by=acting_user(request))
    except ValueError as exc:
        raise HTTPException(409, f"Callback is already {exc}.") from None
    if result is None:
        raise HTTPException(404, "No such callback.")
    return result


@dashboard.get("/messages")
async def list_messages(request: Request, phone: str, call_sid: str | None = None):
    api: Api = request.app.state.api
    _phone_or_400(phone)
    return {
        "phone": phone,
        "call_sid": call_sid,
        "messages": await sms.list_messages(api, phone, call_sid=call_sid),
    }


@dashboard.get("/consent")
async def get_consent(request: Request, phone: str):
    api: Api = request.app.state.api
    _phone_or_400(phone)
    history = await sms.consent_history(api, phone)
    live = [d for d in history if not d.get("withdrawn_at")]
    return {
        "phone": phone,
        "granted": bool(live),
        "consent": live[-1] if live else None,
        "history": history,
    }


@dashboard.post("/consent")
async def set_consent(body: ConsentInput, request: Request):
    """Record (or withdraw) a number's agreement to receive texts.

    Separate from the dial allowlist on purpose: A2P 10DLC forbids making message
    consent a condition of the service, so the two are granted and revoked
    independently. Withdrawal stamps the history; nothing is deleted (S-6).
    """
    api: Api = request.app.state.api
    by = body.granted_by or acting_user(request)
    if body.granted:
        await sms.grant_consent(
            api,
            body.phone,
            statement_version=body.statement_version,
            granted_by=by,
            source="dashboard",
        )
    else:
        await sms.withdraw_consent(api, body.phone, by=by, reason="withdrawn in dashboard")
    log.info(
        "sms consent changed",
        extra={"event": "sms.consent", "granted": body.granted, "to": mask_phone(body.phone)},
    )
    return {"phone": body.phone, "granted": body.granted}


@dashboard.get("/settings")
async def get_settings(request: Request):
    api: Api = request.app.state.api
    return {"settings": await sms.get_settings(api), "defaults": sms.SETTING_DEFAULTS}


@dashboard.post("/settings")
async def set_setting(body: SettingInput, request: Request):
    api: Api = request.app.state.api
    try:
        values = await sms.set_setting(api, body.name, body.value, by=acting_user(request))
    except KeyError:
        raise HTTPException(400, f"Unknown setting {body.name!r}.") from None
    return {"settings": values}


@dashboard.post("/test/start")
async def simulator_start(body: SimStartInput, request: Request):
    """Dry-run simulator: stage a missed call for the fictional test patient and
    send the invite into the thread. Never sends a text (409 unless dry-run)."""
    api: Api = request.app.state.api
    try:
        return await sms_simulator.start(
            api,
            body.phone,
            diabetic=body.diabetic,
            by=acting_user(request),
            language=body.language,
        )
    except sms_simulator.SimulatorError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@dashboard.post("/test/reply")
async def simulator_reply(body: SimReplyInput, request: Request):
    """Dry-run simulator: one patient message through the real inbound handler."""
    api: Api = request.app.state.api
    try:
        return await sms_simulator.reply(api, body.phone, body.body)
    except sms_simulator.SimulatorError as exc:
        raise HTTPException(exc.status, exc.detail) from None


# --------------------------------------------------------------------------- tasks


@tasks.post("/tasks/sms-missed-call")
async def task_missed_call(payload: dict, request: Request):
    api: Api = request.app.state.api
    call_id = str(payload.get("call_id") or "")
    if not call_id:
        return {"skipped": "no call id"}
    return await sms.handle_missed_call(api, call_id)


@tasks.post("/tasks/sms-inbound")
async def task_inbound(payload: dict, request: Request):
    api: Api = request.app.state.api
    try:
        return await sms.handle_inbound(
            api,
            str(payload.get("from") or ""),
            str(payload.get("body") or ""),
            str(payload.get("message_id") or ""),
        )
    except sms.Retryable as exc:
        return _retry(exc)


@tasks.post("/tasks/sms-action")
async def task_action(payload: dict, request: Request):
    api: Api = request.app.state.api
    try:
        return await sms.run_action(api, payload)
    except sms.Retryable as exc:
        return _retry(exc)


@tasks.post("/cron/sms-expire")
async def cron_expire(request: Request):
    api: Api = request.app.state.api
    return await sms.expire_sessions(api)


router.include_router(dashboard)
router.include_router(tasks)
