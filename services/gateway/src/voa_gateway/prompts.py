"""Pre-rendered audio prompts (voicemail drop), via Gemini-TTS, cached in GCS.

Rendering takes several seconds, so it happens once per (text, voice, model) and
is cached in the prompts bucket; instances load the clip at startup. If the clip
is not ready when a voicemail drop is needed, the gateway hangs up instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import audioop
from google import genai
from google.genai import types
from voa_providers.audio import SAMPLE_WIDTH, Resampler

log = logging.getLogger(__name__)


#: The TTS language for a call language, where the TTS model names it
#: differently. es-US is accepted by gemini-2.5-flash-tts (checked live, 22 Oct 2026).
TTS_LANGUAGE: dict[str, str] = {}


def tts_language(code: str | None) -> str | None:
    return TTS_LANGUAGE.get(code or "", code) if code else None


class PromptCache:
    def __init__(
        self, *, project_id: str, location: str, model: str, voice: str, bucket: str | None
    ):
        self._project, self._location, self._model, self._voice = project_id, location, model, voice
        self._bucket = bucket
        self._clips: dict[str, bytes] = {}  # key -> mu-law 8 kHz

    def _key(self, text: str, language_code: str | None = None) -> str:
        # English keys are unchanged (no language part), so cached clips stay valid.
        lang = f"|{tts_language(language_code)}" if language_code else ""
        return hashlib.sha256(f"{self._model}|{self._voice}{lang}|{text}".encode()).hexdigest()[:24]

    def get(self, text: str, language_code: str | None = None) -> bytes | None:
        return self._clips.get(self._key(text, language_code))

    async def ensure(self, text: str, language_code: str | None = None) -> bytes | None:
        """The clip for ``text``. ``language_code``: a non-English clip (the
        pack's voicemail), rendered with that TTS language and cached per language."""
        key = self._key(text, language_code)
        if key in self._clips:
            return self._clips[key]
        try:
            clip = await asyncio.to_thread(self._load_or_render, key, text, language_code)
        except Exception:
            log.exception("prompt render failed", extra={"event": "prompt.render_failed"})
            return None
        self._clips[key] = clip
        return clip

    def _load_or_render(self, key: str, text: str, language_code: str | None = None) -> bytes:
        blob = None
        if self._bucket:
            from google.cloud import storage

            blob = (
                storage.Client(project=self._project)
                .bucket(self._bucket)
                .blob(f"prompts/{key}.ulaw")
            )
            if blob.exists():
                log.info("prompt cache hit", extra={"event": "prompt.cache_hit"})
                return blob.download_as_bytes()
        client = genai.Client(enterprise=True, project=self._project, location=self._location)
        resp = client.models.generate_content(
            model=self._model,
            contents="Read this as a warm, clear voicemail message at a calm pace: " + text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                    ),
                    **({"language_code": tts_language(language_code)} if language_code else {}),
                ),
            ),
        )
        pcm24 = resp.candidates[0].content.parts[0].inline_data.data
        clip = audioop.lin2ulaw(Resampler(24_000, 8_000)(pcm24), SAMPLE_WIDTH)
        if blob is not None:
            blob.upload_from_string(clip, content_type="audio/basic")
        log.info("prompt rendered", extra={"event": "prompt.rendered", "seconds": len(clip) / 8000})
        return clip
