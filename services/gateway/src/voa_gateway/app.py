"""voa-gateway: the only public service.

Routes (every Twilio route verifies X-Twilio-Signature; fail closed without a token):
  GET  /health
  POST /twilio/answer       -> TwiML <Connect><Stream>
  POST /twilio/status       call progress + async AMD verdicts -> calls/{id}; terminal -> task
  POST /twilio/sms          inbound SMS -> task (processed by the API)
  WS   /twilio/media        Twilio Media Streams -> CallSession
  GET  /softphone           browser softphone (only if softphone.enabled)
  WS   /softphone/media     browser media -> CallSession (token required unless local dev)

Slow or stateful work is handed to the private API through Cloud Tasks; webhooks
return immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from voa_agents.voice.agent import build_voice_agent
from voa_core import lifecycle
from voa_core.campaign import load_campaign
from voa_core.config import AppConfig, load_config, resolve_secret
from voa_core.logging import bind, configure_logging, mask_phone, parse_cloud_trace_header
from voa_core.store import DocStore, FirestoreStore, MemoryStore
from voa_core.tasks import CloudTaskQueue, MemoryTaskQueue, TaskQueue
from voa_providers.audio import Codec
from voa_providers.browser import BrowserMediaSession
from voa_providers.twilio import TwilioMediaSession, validate_signature

from .persist import StoreCallSink
from .prompts import PromptCache
from .recording_stream import stream_recording
from .session import AmdPolicy, CallSession

log = logging.getLogger("voa_gateway")
STATIC = Path(__file__).parent / "static"
CALL_ID_RE = re.compile(r"^(CA[0-9a-f]{32}|browser_[0-9a-f]{16}|local_[0-9a-f]{16,32})$")

# Twilio CallStatus -> provider-neutral event kind
_TWILIO_KIND = {
    "queued": lifecycle.CallEventKind.QUEUED,
    "initiated": lifecycle.CallEventKind.QUEUED,
    "ringing": lifecycle.CallEventKind.RINGING,
    "in-progress": lifecycle.CallEventKind.ANSWERED,
    "completed": lifecycle.CallEventKind.COMPLETED,
    "busy": lifecycle.CallEventKind.BUSY,
    "no-answer": lifecycle.CallEventKind.NO_ANSWER,
    "failed": lifecycle.CallEventKind.FAILED,
    "canceled": lifecycle.CallEventKind.CANCELED,
}


class Gateway:
    """Process-wide singletons, built once at startup."""

    def __init__(
        self, cfg: AppConfig, store: DocStore | None = None, tasks: TaskQueue | None = None
    ):
        self.cfg = cfg
        if store is not None:
            self.store = store
        elif cfg.services.store == "memory":
            self.store = MemoryStore()
        else:
            self.store = FirestoreStore(cfg.gcp.project_id)
        if tasks is not None:
            self.tasks = tasks
        elif cfg.services.api_url and cfg.services.tasks_invoker_sa:
            self.tasks = CloudTaskQueue(
                project_id=cfg.gcp.project_id,
                region=cfg.gcp.region,
                target_base_url=cfg.services.api_url,
                invoker_sa=cfg.services.tasks_invoker_sa,
            )
        else:
            self.tasks = MemoryTaskQueue()
        self.sink = StoreCallSink(
            self.store,
            recordings_bucket=cfg.services.recordings_bucket or None,
            project_id=cfg.gcp.project_id,
            call_id_pattern=CALL_ID_RE,
            tasks=self.tasks,
            events_queue=cfg.services.queue_events,
        )
        self.runner = Runner(
            app_name="voa_voice",
            agent=build_voice_agent(cfg),
            session_service=InMemorySessionService(),
        )
        self.prompts = PromptCache(
            project_id=cfg.gcp.project_id,
            location=cfg.models.tts_location,
            model=cfg.models.tts,
            voice=cfg.voice.voice_name,
            bucket=cfg.services.prompts_bucket or None,
        )
        self.amd = AmdPolicy(
            enabled=cfg.amd.enabled,
            hold_max_s=cfg.amd.hold_max_s,
            late_window_s=cfg.amd.late_window_s,
            vm_drop=cfg.amd.vm_drop,
            vm_drop_verdicts=frozenset(cfg.amd.vm_drop_verdicts),
            poll_interval_s=cfg.amd.poll_interval_s,
            strategy=cfg.amd.strategy,
        )
        self._twilio_token: str | None = None
        self._softphone_secret: str | None = None

    def voicemail_text(self, org_name: str | None = None) -> str:
        return load_campaign("post_op").script(
            "voicemail", org_name=org_name or self.cfg.site.org_name
        )

    async def voicemail_clip(self) -> bytes | None:
        # The clip says the company name: a new name renders (and caches) a new clip.
        from voa_core import organization

        org = await organization.org_name(self.store, self.cfg.site.org_name)
        return await self.prompts.ensure(self.voicemail_text(org))

    def _secret(self, ref: str) -> str:
        """Resolve a secret, failing closed: a missing secret or version yields ""
        (callers then refuse the request) instead of a 500. Not cached when empty,
        so adding the secret later takes effect without a redeploy."""
        if not ref:
            return ""
        try:
            return resolve_secret(ref, self.cfg.gcp.project_id)
        except Exception:
            log.error("secret unavailable", extra={"event": "secret.unavailable", "ref": ref})
            return ""

    def twilio_token(self) -> str:
        if not self._twilio_token:
            self._twilio_token = self._secret(
                self.cfg.providers.get("twilio", {}).get("auth_token", "")
            )
        return self._twilio_token

    def softphone_secret(self) -> str:
        if not self._softphone_secret:
            self._softphone_secret = self._secret(self.cfg.softphone.token_secret)
        return self._softphone_secret

    def public(self, path: str, scheme: str = "https") -> str:
        base = self.cfg.services.gateway_url or self.cfg.telephony.public_base_url
        host = urlsplit(base).netloc or base
        return f"{scheme}://{host}{path}"


def verify_softphone_token(secret: str, token: str, now: float | None = None) -> str | None:
    """Token = ``<call_id>.<expiry>.<hmac>``; returns the call id if valid."""
    try:
        call_id, exp, sig = token.split(".")
    except ValueError:
        return None
    want = hmac.new(secret.encode(), f"{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig) or float(exp) < (now or time.time()):
        return None
    return call_id if CALL_ID_RE.match(call_id) else None


def create_app(cfg: AppConfig | None = None, gateway: Gateway | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = cfg or load_config()
        configure_logging(c.log_level, c.gcp.project_id)
        app.state.gw = gateway or Gateway(c)
        if c.amd.enabled and c.amd.vm_drop:
            import asyncio

            asyncio.get_running_loop().create_task(app.state.gw.voicemail_clip())  # warm the clip
        yield

    app = FastAPI(
        title="voa-gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        t, s = parse_cloud_trace_header(request.headers.get("x-cloud-trace-context"))
        with bind(trace=t, span_id=s):
            return await call_next(request)

    def gw(req) -> Gateway:
        return req.app.state.gw

    async def signed_form(request: Request) -> dict | None:
        g = gw(request)
        token = g.twilio_token()
        if not token:
            log.error("twilio auth token not configured", extra={"event": "twilio.no_token"})
            return None
        body = await request.body()
        if len(body) > 64 * 1024:
            return None
        form = dict(await request.form())
        url = g.public(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        return (
            form
            if validate_signature(token, url, form, request.headers.get("x-twilio-signature"))
            else None
        )

    @app.get("/health")
    async def health():
        return {"ok": True}

    # ------------------------------------------------------------------ recordings
    @app.get("/recording/{call_id}")
    async def recording(call_id: str, request: Request):
        """One call's recording, for a valid 15-minute link minted by the private API
        (the nurse agent plays it inside its Gemini Enterprise card). Range requests
        are honoured so the player can seek."""
        from voa_core import links

        g = gw(request)
        q = request.query_params
        if not g.cfg.softphone.token_secret or not links.verify_recording_link(
            g.softphone_secret(), call_id, q.get("exp", ""), q.get("sig", "")
        ):
            return Response(status_code=403)
        call = await g.store.get("calls", call_id)
        uri = str((call or {}).get("recording_uri", ""))
        if not uri.startswith("gs://"):
            return Response(status_code=404)
        log.info("recording played", extra={"event": "recording.served", "call_id": call_id})
        return await stream_recording(g.cfg.gcp.project_id, uri, request.headers.get("range", ""))

    # ------------------------------------------------------------------ twilio voice
    @app.post("/twilio/answer")
    async def twilio_answer(request: Request):
        form = await signed_form(request)
        if form is None:
            return Response(status_code=403)
        g = gw(request)
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
            f'<Stream url="{g.public("/twilio/media", "wss")}"/></Connect></Response>'
        )
        return Response(content=twiml, media_type="application/xml")

    @app.post("/twilio/status")
    async def twilio_status(request: Request):
        form = await signed_form(request)
        if form is None:
            return Response(status_code=403)
        g = gw(request)
        call_id = str(form.get("CallSid", ""))
        if not CALL_ID_RE.match(call_id):
            return Response(status_code=400)
        status = str(form.get("CallStatus") or "")
        answered_by = str(form.get("AnsweredBy") or "") or None
        now = datetime.now(UTC)
        with bind(call_id=call_id, provider="twilio"):
            if not status and answered_by:
                # Async AMD callback: verdict only, no CallStatus (Twilio quirk).
                result: dict = {}

                def amd(cur):
                    if cur is None:
                        return None  # never resurrect a deleted call (review P-9)
                    new, promoted = lifecycle.apply_amd_verdict(cur, answered_by, now=now)
                    result["promoted"] = promoted
                    return new

                await g.store.transact("calls", call_id, amd)
                log.info("amd verdict", extra={"event": "amd.verdict", "verdict": answered_by})
                if result.get("promoted"):
                    await g.tasks.enqueue(
                        g.cfg.services.queue_events,
                        "/tasks/call-terminal",
                        {"call_id": call_id},
                        name=f"terminal-{call_id}-amd",
                    )
                return Response(status_code=204)
            kind = _TWILIO_KIND.get(status, lifecycle.CallEventKind.UNKNOWN)
            written: dict = {}

            def mutate(cur):
                if cur is None:
                    return None  # unknown or deleted call: ignore (P-9)
                patch = lifecycle.call_event_patch(
                    kind,
                    now=now,
                    current=cur,
                    provider_status=status,
                    answered_by=answered_by,
                    duration_seconds=form.get("CallDuration"),
                    failure_code=form.get("ErrorCode") or form.get("SipResponseCode"),
                    failure_message=form.get("ErrorMessage"),
                )
                new = lifecycle.merge_call_doc(cur, patch, now=now)
                written["doc"] = new or cur
                return new

            await g.store.transact("calls", call_id, mutate)
            final = (written.get("doc") or {}).get("call_status")
            log.info(
                "call status",
                extra={"event": "call.status", "provider_status": status, "call_status": final},
            )
            if final in lifecycle.TERMINAL_STATUSES:
                await g.tasks.enqueue(
                    g.cfg.services.queue_events,
                    "/tasks/call-terminal",
                    {"call_id": call_id},
                    name=f"terminal-{call_id}-{final}",
                )
        return Response(status_code=204)

    @app.websocket("/twilio/media")
    async def twilio_media(ws: WebSocket):
        g: Gateway = ws.app.state.gw
        token = g.twilio_token()
        url = g.public(ws.url.path, "wss") + (f"?{ws.url.query}" if ws.url.query else "")
        if not token or not validate_signature(
            token, url, {}, ws.headers.get("x-twilio-signature")
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        session = CallSession(
            cfg=g.cfg,
            runner=g.runner,
            media=TwilioMediaSession(ws),
            sink=g.sink,
            amd=g.amd,
            voicemail_clip=g.voicemail_clip,
        )
        try:
            await session.run()
        except (ConnectionError, TimeoutError):
            log.warning("media stream ended before start", extra={"event": "media.no_start"})

    # ------------------------------------------------------------------ twilio sms
    @app.post("/twilio/sms")
    async def twilio_sms(request: Request):
        form = await signed_form(request)
        if form is None:
            return Response(status_code=403)
        g = gw(request)
        message_id = str(form.get("MessageSid", ""))
        payload = {
            "from": form.get("From", ""),
            "body": form.get("Body", ""),
            "message_id": message_id,
        }
        # Named after MessageSid: Twilio redelivers with the same id (review C-7).
        await g.tasks.enqueue(
            g.cfg.services.queue_sms,
            "/tasks/sms-inbound",
            payload,
            name=f"sms-in-{message_id}" if message_id else None,
        )
        log.info(
            "sms inbound queued",
            extra={"event": "sms.inbound", "from": mask_phone(payload["from"])},
        )
        return Response(content="<Response/>", media_type="application/xml")

    # ------------------------------------------------------------------ softphone
    @app.get("/softphone", response_class=HTMLResponse)
    async def softphone(request: Request):
        if not gw(request).cfg.softphone.enabled:
            return Response(status_code=404)
        return (STATIC / "softphone.html").read_text()

    @app.websocket("/softphone/media")
    async def softphone_media(ws: WebSocket):
        g: Gateway = ws.app.state.gw
        if not g.cfg.softphone.enabled:
            await ws.close(code=1008)
            return
        secret = g.softphone_secret()
        call_id = None
        if secret:
            call_id = verify_softphone_token(secret, ws.query_params.get("token", ""))
            if not call_id:
                await ws.close(code=1008)
                return
        elif g.cfg.env not in ("dev", "test"):
            await ws.close(code=1008)  # no token secret outside local dev: refuse
            return
        await ws.accept()
        media = BrowserMediaSession(ws)
        if call_id:
            media.call_id = call_id
        session = CallSession(
            cfg=g.cfg,
            runner=g.runner,
            media=media,
            sink=g.sink,
            inbound_codec=Codec.PCM16_16K,
            amd=AmdPolicy(enabled=False),
        )
        try:
            await session.run()
        except (ConnectionError, TimeoutError):
            log.warning("softphone ended before start", extra={"event": "media.no_start"})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"event": "http.error", "path": request.url.path})
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app


app = create_app()
