"""Twilio adapter: Programmable Voice + Media Streams."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from .audio import Codec, frames
from .base import (
    Audio,
    Dtmf,
    MarkAck,
    MediaFrame,
    PlaceCallRequest,
    PlacedCall,
    ProviderRejected,
    StreamStart,
    StreamStop,
)

log = logging.getLogger(__name__)

FRAME_BYTES = 160  # 20 ms of mu-law at 8 kHz; Twilio's native frame size


# --------------------------------------------------------------------------- signatures


def public_url(url: str) -> str:
    """Cloud Run terminates TLS, so the app sees http:// and ws:// URLs while
    Twilio signed https:// and wss://. Rewrite the scheme prefix only."""
    if url.startswith("http:"):
        return "https:" + url[5:]
    if url.startswith("ws:"):
        return "wss:" + url[3:]
    return url


def validate_signature(
    auth_token: str, url: str, params: Mapping[str, Any], signature: str | None
) -> bool:
    ok = RequestValidator(auth_token).validate(public_url(url), dict(params), signature or "")
    if not ok:
        # The URL is our own webhook; params may carry phone numbers and are not logged.
        log.warning("twilio signature rejected", extra={"event": "twilio.signature_rejected"})
    return ok


# --------------------------------------------------------------------------- call control


class TwilioVoice:
    name = "twilio"

    def __init__(
        self, *, account_sid: str, api_key_sid: str, api_key_secret: str, from_number: str
    ):
        self._client = Client(api_key_sid, api_key_secret, account_sid)
        self._from = from_number

    async def place_call(self, req: PlaceCallRequest) -> PlacedCall:
        kwargs: dict[str, Any] = {
            "to": req.to,
            "from_": self._from,
            "url": req.answer_url,
            "method": "POST",
            "status_callback": req.status_url,
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            "status_callback_method": "POST",
        }
        if req.amd:
            kwargs |= {
                "machine_detection": req.amd.mode,
                "async_amd": "true",
                "async_amd_status_callback": req.status_url,
                "async_amd_status_callback_method": "POST",
                "machine_detection_timeout": req.amd.timeout_s,
                "machine_detection_speech_end_threshold": req.amd.speech_end_threshold_ms,
            }
        try:
            call = await asyncio.wait_for(
                asyncio.to_thread(self._client.calls.create, **kwargs), 20
            )
        except TwilioRestException as exc:
            raise ProviderRejected(exc.code, "twilio rejected the call") from None
        return PlacedCall(call_id=call.sid, status=call.status)

    async def hangup(self, call_id: str) -> None:
        await asyncio.to_thread(self._client.calls(call_id).update, status="completed")

    def answer_response(self, media_ws_url: str, params: Mapping[str, str]) -> tuple[str, str]:
        resp = VoiceResponse()
        connect = Connect()
        stream = connect.stream(url=media_ws_url)
        for key, value in params.items():
            stream.parameter(name=key, value=value)
        resp.append(connect)
        return str(resp), "application/xml"


# --------------------------------------------------------------------------- media


class _WebSocket(Protocol):  # the subset of starlette.websockets.WebSocket used here
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class TwilioMediaSession:
    """Twilio Media Streams over a FastAPI/Starlette WebSocket."""

    codec = Codec.MULAW_8K

    def __init__(self, ws: _WebSocket):
        self._ws = ws
        self._send_lock = (
            asyncio.Lock()
        )  # one writer at a time; interleaved frames corrupt the stream
        self.stream_id: str | None = None
        self.call_id: str | None = None
        self._closed = False

    async def frames(self) -> AsyncIterator[MediaFrame]:
        from starlette.websockets import WebSocketDisconnect

        while True:
            try:
                raw = await self._ws.receive_text()
            except WebSocketDisconnect:
                yield StreamStop("disconnect")
                return
            msg = json.loads(raw)
            kind = msg.get("event")
            if kind == "media":
                yield Audio(base64.b64decode(msg["media"]["payload"]))
            elif kind == "start":
                start = msg["start"]
                self.stream_id, self.call_id = start["streamSid"], start["callSid"]
                yield StreamStart(self.stream_id, self.call_id, start.get("customParameters") or {})
            elif kind == "mark":
                yield MarkAck(msg["mark"]["name"])
            elif kind == "dtmf":
                yield Dtmf(msg["dtmf"]["digit"])
            elif kind == "stop":
                yield StreamStop("stop")
                return
            # "connected" carries nothing we need.

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed or not self.stream_id:
            return
        async with self._send_lock:
            try:
                await self._ws.send_text(json.dumps({**payload, "streamSid": self.stream_id}))
            except Exception:  # caller hung up mid-send: the call is over, not an error
                self._closed = True

    async def send_audio(self, data: bytes) -> None:
        for chunk in frames(data, FRAME_BYTES):
            await self._send(
                {"event": "media", "media": {"payload": base64.b64encode(chunk).decode()}}
            )

    async def mark(self, name: str) -> None:
        await self._send({"event": "mark", "mark": {"name": name}})

    async def clear(self) -> None:
        await self._send({"event": "clear"})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:  # noqa: S110 - already closed by the peer
            pass
