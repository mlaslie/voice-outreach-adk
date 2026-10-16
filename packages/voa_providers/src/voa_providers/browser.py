"""Browser softphone adapter: lets the whole voice path run without a phone line.

Wire protocol on one WebSocket:

* browser -> server, **binary**: PCM16 mono 16 kHz microphone audio (20-100 ms chunks)
* browser -> server, text JSON: ``{"event":"start","params":{...}}`` once, then
  ``{"event":"mark","name":...}`` when playout reaches a mark, ``{"event":"stop"}``
* server -> browser, **binary**: PCM16 mono 24 kHz agent audio
* server -> browser, text JSON: ``{"event":"mark","name":...}`` and ``{"event":"clear"}``
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .audio import Codec
from .base import Audio, MarkAck, MediaFrame, StreamStart, StreamStop


class _WebSocket(Protocol):
    async def receive(self) -> dict[str, Any]: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class BrowserMediaSession:
    # Inbound is 16 kHz from the mic; outbound plays the model's native 24 kHz.
    codec = Codec.PCM16_24K
    inbound_codec = Codec.PCM16_16K

    def __init__(self, ws: _WebSocket):
        self._ws = ws
        self._lock = asyncio.Lock()
        self._closed = False
        self.call_id = "browser_" + uuid.uuid4().hex[:16]

    async def frames(self) -> AsyncIterator[MediaFrame]:
        while True:
            msg = await self._ws.receive()
            if msg["type"] == "websocket.disconnect":
                yield StreamStop("disconnect")
                return
            if msg.get("bytes") is not None:
                yield Audio(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("event")
            if kind == "start":
                yield StreamStart(self.call_id, self.call_id, data.get("params") or {})
            elif kind == "mark":
                yield MarkAck(str(data.get("name", "")))
            elif kind == "stop":
                yield StreamStop("stop")
                return

    async def send_audio(self, data: bytes) -> None:
        if not self._closed:
            async with self._lock:
                await self._ws.send_bytes(data)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if not self._closed:
            async with self._lock:
                await self._ws.send_text(json.dumps(payload))

    async def mark(self, name: str) -> None:
        await self._send_json({"event": "mark", "name": name})

    async def clear(self) -> None:
        await self._send_json({"event": "clear"})

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                await self._ws.close()
            except RuntimeError:
                pass
