"""Stereo call recording made by the gateway (provider-independent).

Left channel = caller, right channel = agent, 8 kHz PCM16 (telephony quality).
The caller stream arrives continuously in real time, so it is the clock. Agent
audio arrives faster than real time; each chunk is placed at
``max(agent_cursor, caller_cursor)``, which is when it actually starts playing.
On barge-in (``clear``) unplayed agent audio is cut, as it was never heard.
"""

from __future__ import annotations

import io
import wave

import audioop
from voa_providers.audio import SAMPLE_WIDTH, Resampler

RATE = 8_000


class StereoRecorder:
    def __init__(self, caller_rate: int, agent_rate: int, max_seconds: int = 3600):
        self._caller = bytearray()
        self._agent = bytearray()
        self._agent_cursor = 0  # bytes
        self._in = Resampler(caller_rate, RATE)
        self._out = Resampler(agent_rate, RATE)
        self._max = max_seconds * RATE * SAMPLE_WIDTH

    @property
    def seconds(self) -> float:
        return len(self._caller) / (RATE * SAMPLE_WIDTH)

    def caller(self, pcm: bytes) -> None:
        if len(self._caller) < self._max:
            self._caller += self._in(pcm)

    def agent(self, pcm: bytes) -> None:
        data = self._out(pcm)
        start = max(self._agent_cursor, len(self._caller))
        if start + len(data) > self._max:
            return
        if len(self._agent) < start:
            self._agent += b"\x00" * (start - len(self._agent))
        self._agent[start : start + len(data)] = data
        self._agent_cursor = start + len(data)

    def clear(self) -> None:
        """Barge-in: agent audio after 'now' was never played."""
        now = len(self._caller)
        del self._agent[now:]
        self._agent_cursor = now

    def wav(self) -> bytes:
        n = max(len(self._caller), len(self._agent))
        left = bytes(self._caller) + b"\x00" * (n - len(self._caller))
        right = bytes(self._agent) + b"\x00" * (n - len(self._agent))
        stereo = audioop.add(
            audioop.tostereo(left, SAMPLE_WIDTH, 1, 0),
            audioop.tostereo(right, SAMPLE_WIDTH, 0, 1),
            SAMPLE_WIDTH,
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(SAMPLE_WIDTH)
            w.setframerate(RATE)
            w.writeframes(stereo)
        return buf.getvalue()
