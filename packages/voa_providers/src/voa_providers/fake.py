"""Scripted in-memory media session for tests and the offline spike harness.

It behaves like a telephony leg: audio sent to it "plays out" in real time, marks
are acknowledged only after everything queued before them has played, and
``clear`` discards unplayed audio. Tests push inbound frames with :meth:`feed`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .audio import Codec, duration_ms
from .base import Audio, MarkAck, MediaFrame, StreamStart, StreamStop


@dataclass
class PlayedChunk:
    at: float  # monotonic time playout started
    data: bytes


@dataclass
class FakeMediaSession:
    codec: Codec = Codec.MULAW_8K
    call_id: str = "fake_call"
    realtime: bool = True  # False = audio "plays" instantly (unit tests)
    played: list[PlayedChunk] = field(default_factory=list)
    events: list[tuple[float, str]] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        self._inbound: asyncio.Queue[MediaFrame] = asyncio.Queue()
        self._playout_until = 0.0  # monotonic time when queued audio finishes
        self._pending_marks: list[tuple[asyncio.Task, str]] = []
        self._generation = 0  # bumped by clear() to cancel scheduled marks

    # -------------------------------------------------- test-side controls
    def start(self, **params: str) -> None:
        self._inbound.put_nowait(StreamStart(self.call_id, self.call_id, params))

    def feed(self, data: bytes) -> None:
        self._inbound.put_nowait(Audio(data))

    def hangup(self) -> None:
        self._inbound.put_nowait(StreamStop("remote_hangup"))

    @property
    def playout_remaining_s(self) -> float:
        return max(0.0, self._playout_until - time.monotonic())

    # -------------------------------------------------- MediaSession
    async def frames(self) -> AsyncIterator[MediaFrame]:
        while True:
            frame = await self._inbound.get()
            yield frame
            if isinstance(frame, StreamStop):
                return

    async def send_audio(self, data: bytes) -> None:
        if self.closed or not data:
            return
        now = time.monotonic()
        start = max(now, self._playout_until)
        self.played.append(PlayedChunk(start, data))
        if self.realtime:
            self._playout_until = start + duration_ms(data, self.codec) / 1000

    async def mark(self, name: str) -> None:
        delay = self.playout_remaining_s
        gen = self._generation

        async def ack() -> None:
            await asyncio.sleep(delay)
            if gen == self._generation and not self.closed:
                self.events.append((time.monotonic(), f"mark:{name}"))
                await self._inbound.put(MarkAck(name))

        self._pending_marks.append((asyncio.create_task(ack()), name))

    async def clear(self) -> None:
        self.events.append((time.monotonic(), "clear"))
        self._playout_until = time.monotonic()
        self._generation += 1
        # Twilio echoes outstanding marks immediately when the buffer is cleared.
        for task, name in self._pending_marks:
            if not task.done():
                task.cancel()
                self._inbound.put_nowait(MarkAck(name))
        self._pending_marks.clear()

    async def close(self) -> None:
        self.closed = True
        self.events.append((time.monotonic(), "close"))
        for task, _ in self._pending_marks:
            task.cancel()


@dataclass
class FakeVoice:
    """Voice provider double: records placed calls, can be told to reject."""

    name: str = "fake"
    placed: list = field(default_factory=list)
    reject_code: int | None = None

    async def place_call(self, req):
        from uuid import uuid4

        from .base import PlacedCall, ProviderRejected

        if self.reject_code is not None:
            raise ProviderRejected(self.reject_code, "rejected")
        call_id = "CA" + uuid4().hex
        self.placed.append((call_id, req))
        return PlacedCall(call_id=call_id, status="queued")

    async def hangup(self, call_id: str) -> None:
        return None

    def answer_response(self, media_ws_url, params):
        return "<Response/>", "application/xml"
