import asyncio
import base64
import json

from voa_providers.audio import Codec, InboundTranscoder, OutboundTranscoder, duration_ms, silence
from voa_providers.base import Audio, MarkAck, StreamStart, StreamStop
from voa_providers.fake import FakeMediaSession
from voa_providers.twilio import FRAME_BYTES, TwilioMediaSession, public_url


def test_transcode_sizes():
    inbound = InboundTranscoder(Codec.MULAW_8K)
    out = b"".join(inbound(silence(20, Codec.MULAW_8K)) for _ in range(50))  # 1 s
    assert abs(len(out) - 32_000) <= 8  # 16 kHz * 2 bytes; ratecv may hold back a sample
    outbound = OutboundTranscoder(Codec.MULAW_8K)
    ulaw = outbound(b"\x00\x00" * 24_000)  # 1 s at 24 kHz
    assert abs(len(ulaw) - 8_000) <= 4
    assert duration_ms(ulaw, Codec.MULAW_8K) == len(ulaw) / 8


def test_public_url_rewrites_scheme_prefix_only():
    assert public_url("http://h/x?u=http://y") == "https://h/x?u=http://y"
    assert public_url("ws://h/media") == "wss://h/media"


class _FakeWs:
    def __init__(self, incoming):
        self.incoming, self.sent = list(incoming), []

    async def receive_text(self):
        return self.incoming.pop(0)

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000):
        pass


async def test_twilio_media_session_parses_and_frames():
    payload = base64.b64encode(b"\xff" * 160).decode()
    ws = _FakeWs(
        [
            json.dumps({"event": "connected"}),
            json.dumps(
                {
                    "event": "start",
                    "start": {"streamSid": "MZ1", "callSid": "CA1", "customParameters": {"k": "v"}},
                }
            ),
            json.dumps({"event": "media", "media": {"payload": payload}}),
            json.dumps({"event": "mark", "mark": {"name": "m1"}}),
            json.dumps({"event": "stop"}),
        ]
    )
    s = TwilioMediaSession(ws)
    got = [f async for f in s.frames()]
    assert isinstance(got[0], StreamStart) and got[0].params == {"k": "v"}
    assert isinstance(got[1], Audio) and len(got[1].data) == 160
    assert got[2] == MarkAck("m1") and isinstance(got[3], StreamStop)
    await s.send_audio(b"\x00" * (FRAME_BYTES * 2 + 10))
    await s.clear()
    assert [m["event"] for m in ws.sent] == ["media", "media", "media", "clear"]
    assert all(m["streamSid"] == "MZ1" for m in ws.sent)


async def test_fake_mark_waits_for_playout_and_clear_echoes_marks():
    m = FakeMediaSession(codec=Codec.MULAW_8K)
    await m.send_audio(silence(200, Codec.MULAW_8K))
    await m.mark("a")
    frames = m.frames()
    got = await asyncio.wait_for(frames.__anext__(), 1)
    assert got == MarkAck("a") and m.playout_remaining_s == 0

    await m.send_audio(silence(5000, Codec.MULAW_8K))
    await m.mark("b")
    await m.clear()
    assert await asyncio.wait_for(frames.__anext__(), 0.2) == MarkAck("b")
