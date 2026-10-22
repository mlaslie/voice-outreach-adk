"""The demo auto-approve on the gateway (docs/LANGUAGES-PLAN.md, "Language
availability and approval"): a language without a pack is spoken with the
English wording translated live (source "machine"); a draft pack is used; the
voicemail is machine-translated, with the English clip as the fallback.
Fictional data only."""

import pytest
import test_gateway_use_cases as base
from test_gateway_use_cases import _state_for, cfg
from voa_core import languages, translate
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.translate import FakeTranslator
from voa_gateway.app import Gateway


@pytest.fixture(autouse=True)
def _reset():
    languages.configure(None)
    translate.clear_outbound_cache()
    yield
    languages.configure(None)
    translate.clear_outbound_cache()


def auto_cfg():
    return load_config(
        environ={
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__STORE": "memory",
            "VOA_ENV": "demo",
            "VOA__LANGUAGES__AUTO_APPROVE": "true",
        }
    )


@pytest.fixture
def auto(monkeypatch):
    # Every load_config applies the switch, so the sessions are built with it on.
    monkeypatch.setattr(base, "cfg", auto_cfg)
    auto_cfg()
    assert languages.auto_approved()


async def test_a_language_without_a_pack_is_spoken_with_machine_wording(monkeypatch, auto):
    s, state = await _state_for(MemoryStore(), monkeypatch, {}, language="vi-VN")
    assert s.call_language == "vi-VN" and s.call_language_source == "machine"
    assert state["call_language"] == "vi-VN" and state["call_language_source"] == "machine"
    assert "language" not in state["patient"]
    # The knowledge base stays off for a non-English call.
    assert "knowledge" not in state
    assert {"event": "language", "code": "vi-VN", "source": "machine"}.items() <= next(
        e for e in s.timeline.entries if e["event"] == "language"
    ).items()


async def test_the_spanish_draft_pack_is_used(monkeypatch, auto):
    s, state = await _state_for(MemoryStore(), monkeypatch, {}, language="es-US")
    assert state["call_language"] == "es-US" and s.call_language_source == "draft_pack"
    # The instruction uses the pack's language block (only "machine" changes it).
    assert state["call_language_source"] == "draft_pack"


async def test_call_in_english_still_forces_english(monkeypatch, auto):
    s, state = await _state_for(
        MemoryStore(), monkeypatch, {}, language="vi-VN", call_in_english=True
    )
    assert "call_language" not in state and s.call_language is None


async def test_off_a_language_without_a_pack_stays_english(monkeypatch):
    s, state = await _state_for(MemoryStore(), monkeypatch, {}, language="vi-VN")
    assert "call_language" not in state and s.call_language_source is None


class Prompts:
    def __init__(self):
        self.calls = []

    async def ensure(self, text, language_code=None):
        self.calls.append((text, language_code))
        return b"clip"


def _gw(translator):
    gw = Gateway(cfg(), store=MemoryStore())
    gw.prompts = Prompts()
    gw.translator = translator
    return gw


async def test_the_voicemail_is_machine_translated_and_cached_per_language():
    t = FakeTranslator(out=lambda text, target: f"Xin chào ({target})")
    gw = _gw(t)
    await gw.voicemail_clip("post_op", language="vi-VN", source="machine")
    await gw.voicemail_clip("post_op", language="vi-VN", source="machine")
    assert gw.prompts.calls == [("Xin chào (vi-VN)", "vi-VN")] * 2
    assert len(t.out_calls) == 1  # translated once, then cached
    english = gw.voicemail_text("Acme Health", "post_op")
    assert t.out_calls[0] == ([english], "vi-VN")


async def test_a_failed_voicemail_translation_plays_the_english_clip():
    gw = _gw(FakeTranslator(fail_out=True))
    await gw.voicemail_clip("post_op", language="vi-VN", source="machine")
    english = gw.voicemail_text("Acme Health", "post_op")
    assert gw.prompts.calls == [(english, None)]  # the English clip, English voice


async def test_english_and_pack_voicemails_are_unchanged():
    gw = _gw(FakeTranslator(fail_out=True))
    await gw.voicemail_clip("post_op")
    await gw.voicemail_clip("post_op", language="es-US")
    assert [(t[:12], lc) for t, lc in gw.prompts.calls] == [
        ("Hello, this ", None),
        ("Hola, le lla", "es-US"),
    ]
