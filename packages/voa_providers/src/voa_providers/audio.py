"""Audio transcoding between telephony codecs and the Gemini Live API.

Gemini Live wants **16-bit PCM, 16 kHz, mono, little-endian** in and returns
**16-bit PCM at 24 kHz**. Telephony providers usually carry **G.711 mu-law at
8 kHz**. ``audioop.ratecv`` keeps fractional-sample state between chunks; each
direction of each call needs its own :class:`Resampler`, or chunk boundaries
click.

``audioop`` left the stdlib in Python 3.13; ``audioop-lts`` provides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import audioop

SAMPLE_WIDTH = 2  # 16-bit
LIVE_INPUT_RATE = 16_000
LIVE_OUTPUT_RATE = 24_000
LIVE_INPUT_MIME = f"audio/pcm;rate={LIVE_INPUT_RATE}"


class Codec(Enum):
    MULAW_8K = ("mulaw", 8_000)
    PCM16_16K = ("pcm16", 16_000)
    PCM16_24K = ("pcm16", 24_000)

    @property
    def rate(self) -> int:
        return self.value[1]

    @property
    def is_mulaw(self) -> bool:
        return self.value[0] == "mulaw"

    def bytes_per_ms(self) -> float:
        return self.rate / 1000 * (1 if self.is_mulaw else SAMPLE_WIDTH)


@dataclass
class Resampler:
    """Stateful mono 16-bit resampler for one direction of one call."""

    src_rate: int
    dst_rate: int
    _state: object = field(default=None, repr=False)

    def __call__(self, pcm: bytes) -> bytes:
        if self.src_rate == self.dst_rate or not pcm:
            return pcm
        out, self._state = audioop.ratecv(
            pcm, SAMPLE_WIDTH, 1, self.src_rate, self.dst_rate, self._state
        )
        return out


class InboundTranscoder:
    """Provider codec -> Live input (PCM16 16 kHz)."""

    def __init__(self, codec: Codec):
        self.codec = codec
        self._resample = Resampler(codec.rate, LIVE_INPUT_RATE)

    def __call__(self, data: bytes) -> bytes:
        pcm = audioop.ulaw2lin(data, SAMPLE_WIDTH) if self.codec.is_mulaw else data
        return self._resample(pcm)


class OutboundTranscoder:
    """Live output (PCM16 24 kHz) -> provider codec."""

    def __init__(self, codec: Codec):
        self.codec = codec
        self._resample = Resampler(LIVE_OUTPUT_RATE, codec.rate)

    def __call__(self, pcm24k: bytes) -> bytes:
        pcm = self._resample(pcm24k)
        return audioop.lin2ulaw(pcm, SAMPLE_WIDTH) if self.codec.is_mulaw else pcm


def frames(data: bytes, frame_bytes: int) -> list[bytes]:
    """Split into fixed-size frames (last one may be short)."""
    return [data[i : i + frame_bytes] for i in range(0, len(data), frame_bytes)]


def duration_ms(data: bytes, codec: Codec) -> float:
    return len(data) / codec.bytes_per_ms()


def rms(pcm16: bytes) -> int:
    return audioop.rms(pcm16, SAMPLE_WIDTH) if pcm16 else 0


def silence(ms: int, codec: Codec) -> bytes:
    n = int(codec.bytes_per_ms() * ms)
    return (b"\xff" if codec.is_mulaw else b"\x00") * n  # 0xFF is mu-law silence
