"""Spike server: real phone calls (Twilio) and a browser softphone into the same bridge.

    VOA_ENV=dev uv run uvicorn voice_spike.server:app --port 8080

Routes
  GET  /healthz
  GET  /softphone              browser softphone page (talk to the agent with your mic)
  WS   /browser/media          softphone media
  POST /twilio/answer          Twilio voice webhook -> TwiML <Connect><Stream>
  WS   /twilio/media           Twilio Media Streams
  POST /twilio/status          Twilio status callback (logged only in the spike)

Every Twilio route validates X-Twilio-Signature. The caller is the fictional test
patient from ``harness.PATIENT``; no real patient data is used in phase 0.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from voa_agents.voice.agent import build_voice_agent
from voa_core.config import AppConfig, load_config, resolve_secret
from voa_core.logging import bind, configure_logging, parse_cloud_trace_header
from voa_providers.audio import Codec
from voa_providers.browser import BrowserMediaSession
from voa_providers.twilio import TwilioMediaSession, validate_signature

from .bridge import CallBridge
from .harness import PATIENT

log = logging.getLogger("voice_spike.server")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="voa voice spike", docs_url=None, redoc_url=None, openapi_url=None)


@cache
def cfg() -> AppConfig:
    c = load_config()
    configure_logging(c.log_level, c.gcp.project_id)
    return c


@cache
def runner() -> Runner:
    return Runner(
        app_name="voice_spike",
        agent=build_voice_agent(cfg()),
        session_service=InMemorySessionService(),
    )


@cache
def twilio_auth_token() -> str:
    twilio = cfg().providers.get("twilio", {})
    return resolve_secret(twilio.get("auth_token", ""), cfg().gcp.project_id)


def _public(path: str, scheme: str = "https") -> str:
    base = cfg().telephony.public_base_url.rstrip("/")
    host = urlsplit(base).netloc or base
    return f"{scheme}://{host}{path}"


@app.middleware("http")
async def trace_context(request: Request, call_next):
    trace, span = parse_cloud_trace_header(request.headers.get("x-cloud-trace-context"))
    with bind(trace=trace, span_id=span):
        return await call_next(request)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


# --------------------------------------------------------------------------- browser


@app.get("/softphone", response_class=HTMLResponse)
async def softphone() -> str:
    return (STATIC / "softphone.html").read_text()


@app.websocket("/browser/media")
async def browser_media(ws: WebSocket) -> None:
    await ws.accept()
    media = BrowserMediaSession(ws)
    bridge = CallBridge(
        cfg=cfg(),
        runner=runner(),
        media=media,
        inbound_codec=Codec.PCM16_16K,
        patient=dict(PATIENT),
    )
    result = await bridge.run()
    log.info(
        "browser call finished",
        extra={"event": "call.summary", "outcome": (result.get("report") or {}).get("outcome")},
    )


# --------------------------------------------------------------------------- twilio


async def _form_valid(request: Request) -> tuple[bool, dict]:
    form = dict(await request.form())
    url = _public(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
    return validate_signature(
        twilio_auth_token(), url, form, request.headers.get("x-twilio-signature")
    ), form


@app.post("/twilio/answer")
async def twilio_answer(request: Request) -> Response:
    ok, _form = await _form_valid(request)
    if not ok:
        return Response(status_code=403)
    # No PHI in TwiML parameters: they end up in Twilio's logs. The bridge looks up
    # the patient server-side (here: the fictional test patient).
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f'<Stream url="{_public("/twilio/media", "wss")}"/>'
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/status")
async def twilio_status(request: Request) -> Response:
    ok, form = await _form_valid(request)
    if not ok:
        return Response(status_code=403)
    log.info(
        "twilio status",
        extra={
            "event": "call.status",
            "call_status": form.get("CallStatus"),
            "answered_by": form.get("AnsweredBy"),
            "duration_s": form.get("CallDuration"),
        },
    )
    return Response(status_code=204)


@app.websocket("/twilio/media")
async def twilio_media(ws: WebSocket) -> None:
    url = _public(ws.url.path, "wss") + (f"?{ws.url.query}" if ws.url.query else "")
    if not validate_signature(twilio_auth_token(), url, {}, ws.headers.get("x-twilio-signature")):
        await ws.close(code=1008)
        return
    await ws.accept()
    media = TwilioMediaSession(ws)
    bridge = CallBridge(cfg=cfg(), runner=runner(), media=media, patient=dict(PATIENT))
    result = await bridge.run()
    log.info(
        "twilio call finished",
        extra={"event": "call.summary", "outcome": (result.get("report") or {}).get("outcome")},
    )
