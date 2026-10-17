"""Deployed smoke test: a scripted caller talks to the live gateway over the softphone path.

    uv run python scripts/e2e/softphone_smoke.py

1. stages a FICTIONAL patient in pending_calls/{call_id} (as the API does),
2. mints a softphone token with the HMAC key from Secret Manager,
3. connects to wss://<gateway>/softphone/media and plays Gemini-TTS answers,
4. hangs up after identity verification and checks Firestore for the call's
   transcript and event timeline (the patient doc must have been read once).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import sys
import time
import uuid

from google import genai
from google.genai import types
from voa_core.config import load_config, resolve_secret
from voa_core.store import FirestoreStore
from voa_providers.audio import Resampler
from websockets.asyncio.client import connect

LINES = ["Yes, this is Jordan.", "March fifteenth, nineteen fifty."]


def tts16k(client, model: str, text: str) -> bytes:
    r = client.models.generate_content(
        model=model,
        contents="Say naturally, like answering the phone: " + text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        ),
    )
    return Resampler(24_000, 16_000)(r.candidates[0].content.parts[0].inline_data.data)


async def main() -> int:
    cfg = load_config()
    gateway = sys.argv[1] if len(sys.argv) > 1 else cfg.services.gateway_url
    assert gateway, "pass the gateway URL or set services.gateway_url"
    store = FirestoreStore(cfg.gcp.project_id)
    call_id = "browser_" + uuid.uuid4().hex[:16]
    await store.set(
        "pending_calls",
        call_id,
        {
            "first_name": "Jordan",
            "last_name": "Rivera",
            "dob": "1950-03-15",
            "patient_id": "SMOKE-0001",
            "is_diabetic": False,
        },
    )
    key = resolve_secret("secret://softphone-token-key", cfg.gcp.project_id)
    exp = str(int(time.time()) + 300)
    sig = hmac.new(key.encode(), f"{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    url = gateway.replace("https://", "wss://") + f"/softphone/media?token={call_id}.{exp}.{sig}"

    tts = genai.Client(
        enterprise=True, project=cfg.gcp.project_id, location=cfg.models.tts_location
    )
    clips = [await asyncio.to_thread(tts16k, tts, cfg.models.tts, t) for t in LINES]
    t0 = time.monotonic()
    first_audio = None
    async with connect(url) as ws:
        await ws.send(json.dumps({"event": "start", "params": {}}))
        speech: asyncio.Queue[bytes] = asyncio.Queue()

        async def feed():
            nxt = time.monotonic()
            buf = b""
            while True:
                if not buf and not speech.empty():
                    buf = speech.get_nowait()
                chunk, buf = (buf[:1280], buf[1280:]) if buf else (b"\x00" * 1280, b"")
                await ws.send(chunk)  # 40 ms of 16 kHz PCM16
                nxt += 0.04
                await asyncio.sleep(max(0, nxt - time.monotonic()))

        feeder = asyncio.create_task(feed())
        turns, last_audio = 0, time.monotonic()
        try:
            while turns <= len(LINES) and time.monotonic() - t0 < 90:
                try:
                    msg = await asyncio.wait_for(ws.recv(), 1.0)
                except TimeoutError:
                    msg = None
                if isinstance(msg, bytes):
                    first_audio = first_audio or time.monotonic() - t0
                    last_audio = time.monotonic()
                    continue
                # 1.2 s after the agent's audio stops, answer with the next line.
                if first_audio and time.monotonic() - last_audio > 1.2 and speech.empty():
                    if turns < len(LINES):
                        speech.put_nowait(clips[turns])
                        print(f"caller: {LINES[turns]}")
                    turns += 1
                    last_audio = time.monotonic() + 3  # give the agent time to start
        finally:
            feeder.cancel()
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"event": "stop"}))
    print(f"first agent audio after {first_audio:.2f}s")
    await asyncio.sleep(4)  # let the gateway persist
    events = (await store.get("call_events", call_id) or {}).get("timeline", [])
    turns_doc = (await store.get("transcripts", call_id) or {}).get("turns", [])
    pending = await store.get("pending_calls", call_id)
    names = [e["event"] + (":" + e.get("name", "") if e.get("name") else "") for e in events]
    print(
        "timeline:", [n for n in names if n.startswith(("tool", "agent.release", "media", "call"))]
    )
    print("transcript:", [(t["role"], t["text"][:60]) for t in turns_doc])
    ok = (
        pending is None
        and any(n.startswith("tool.call:verify_identity") for n in names)
        and any(t["role"] == "patient" for t in turns_doc)
    )
    for coll in ("call_events", "transcripts", "calls"):
        await store.delete(coll, call_id)
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
