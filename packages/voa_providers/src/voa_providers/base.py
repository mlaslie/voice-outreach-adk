"""Provider-neutral telephony interfaces.

Nothing outside ``voa_providers`` imports a vendor SDK. Services talk to these
protocols; adapters (``twilio``, ``browser``, ``fake``) implement them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .audio import Codec

# --------------------------------------------------------------------------- media frames


@dataclass(frozen=True)
class StreamStart:
    stream_id: str
    call_id: str
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Audio:
    data: bytes  # in the provider's native codec


@dataclass(frozen=True)
class Dtmf:
    digit: str


@dataclass(frozen=True)
class MarkAck:
    name: str


@dataclass(frozen=True)
class StreamStop:
    reason: str = ""


MediaFrame = StreamStart | Audio | Dtmf | MarkAck | StreamStop


@runtime_checkable
class MediaSession(Protocol):
    """One live call's media channel (usually a provider WebSocket)."""

    codec: Codec

    def frames(self) -> AsyncIterator[MediaFrame]:
        """Inbound frames until the stream stops or the socket closes."""
        ...

    async def send_audio(self, data: bytes) -> None:
        """Queue audio for playout. ``data`` is already in ``codec``."""
        ...

    async def mark(self, name: str) -> None:
        """Ask the provider to echo ``MarkAck(name)`` once everything queued so far has played."""
        ...

    async def clear(self) -> None:
        """Drop all queued-but-unplayed outbound audio (barge-in, voicemail drop)."""
        ...

    async def close(self) -> None:
        """End the media stream; for stream-connected calls this hangs up."""
        ...


# --------------------------------------------------------------------------- call control


@dataclass(frozen=True)
class AmdOptions:
    mode: str = "DetectMessageEnd"
    timeout_s: int = 55
    speech_end_threshold_ms: int = 1000


@dataclass(frozen=True)
class PlaceCallRequest:
    to: str
    answer_url: str
    status_url: str
    amd: AmdOptions | None = None


@dataclass(frozen=True)
class PlacedCall:
    call_id: str
    status: str


class ProviderRejected(Exception):
    """The provider refused the request (bad number, not permitted, ...)."""

    def __init__(self, code: str | int | None, message: str):
        super().__init__(message)
        self.code = code


class VoiceProvider(Protocol):
    name: str

    async def place_call(self, req: PlaceCallRequest) -> PlacedCall: ...

    async def hangup(self, call_id: str) -> None: ...

    def answer_response(self, media_ws_url: str, params: Mapping[str, str]) -> tuple[str, str]:
        """Body and content type that tell the provider to open a media stream."""
        ...
