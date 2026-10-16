"""Spike (c): render the voicemail-drop clip with Gemini-TTS into 8 kHz mu-law.

    uv run python -m voice_spike.render_voicemail [voice]

Writes spikes/voice/out/voicemail_<voice>.ulaw and .wav (gitignored: the clip
speaks the org name). In the real system this runs at deploy time and the clip
goes to the prompts bucket, keyed by a hash of text + voice + model.
"""

from __future__ import annotations

import sys
import time
import wave

import audioop
from google import genai
from google.genai import types
from voa_core.config import load_config
from voa_providers.audio import Resampler

from .harness import OUT

TEXT = (
    "Hello, this is {org} calling for a patient follow-up. "
    "Please call us back at your convenience at the number on your paperwork. Thank you."
)


def main(voice: str = "Aoede") -> None:
    cfg = load_config()
    client = genai.Client(
        enterprise=True, project=cfg.gcp.project_id, location=cfg.models.tts_location
    )
    t = time.monotonic()
    resp = client.models.generate_content(
        model=cfg.models.tts,
        contents="Read this as a warm, clear voicemail message at a calm pace: "
        + TEXT.format(org=cfg.site.org_name),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    pcm24 = resp.candidates[0].content.parts[0].inline_data.data
    pcm8 = Resampler(24_000, 8_000)(pcm24)
    ulaw = audioop.lin2ulaw(pcm8, 2)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"voicemail_{voice}.ulaw").write_bytes(ulaw)
    with wave.open(str(OUT / f"voicemail_{voice}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm8)
    print(
        f"model={cfg.models.tts} voice={voice} render_s={time.monotonic() - t:.2f} "
        f"clip_s={len(ulaw) / 8000:.2f} bytes={len(ulaw)}"
    )


if __name__ == "__main__":
    main(*sys.argv[1:2])
