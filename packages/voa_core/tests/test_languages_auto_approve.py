"""The demo-only language auto-approve (docs/LANGUAGES-PLAN.md, "Language
availability and approval"): every language available, wording per source,
the env guard, and the machine-translated SMS wording helpers. Fictional data."""

from __future__ import annotations

import asyncio

import pytest
from voa_core import language_packs as lp
from voa_core import languages, translate
from voa_core.config import ConfigError, load_config
from voa_core.sms.conversation import machine_language, session_copy
from voa_core.sms_copy import ENGLISH_COPY, MACHINE_SOURCE, PackCopy
from voa_core.translate import FakeTranslator, translate_outbound

BASE = {"VOA__GCP__PROJECT_ID": "test-project"}


def _cfg(env: str, on: bool = True):
    return load_config(
        environ={**BASE, "VOA_ENV": env, "VOA__LANGUAGES__AUTO_APPROVE": str(on).lower()}
    )


@pytest.fixture(autouse=True)
def _reset():
    languages.configure(None)
    translate.clear_outbound_cache()
    yield
    languages.configure(None)
    translate.clear_outbound_cache()


@pytest.fixture
def auto():
    _cfg("test")
    assert languages.auto_approved()


# --------------------------------------------------------------------------- the guard


@pytest.mark.parametrize("env", ["prod", "production", "staging"])
def test_the_switch_is_refused_at_startup_outside_dev_test_demo(env):
    with pytest.raises(ConfigError, match="demo-only"):
        _cfg(env)
    assert not languages.auto_approved()


@pytest.mark.parametrize("env", ["dev", "test", "demo"])
def test_the_switch_is_honoured_in_dev_test_and_demo(env):
    cfg = _cfg(env)
    assert cfg.languages.auto_approve and languages.auto_approved()


def test_off_by_default_and_prod_without_it_starts():
    cfg = load_config(environ={**BASE, "VOA_ENV": "prod"})
    assert cfg.languages.auto_approve is False and not languages.auto_approved()


def test_configure_refuses_a_config_built_some_other_way():
    class Fake:
        env = "prod"

        class languages:  # noqa: N801
            auto_approve = True

    with pytest.raises(languages.AutoApproveRefused):
        languages.configure(Fake())
    assert not languages.auto_approved()


def test_loading_a_config_without_the_switch_turns_it_off_again(auto):
    load_config(environ={**BASE, "VOA_ENV": "test"})
    assert not languages.auto_approved()


# --------------------------------------------------------------------------- availability


def test_off_nothing_changes():
    assert languages.available("post_op") == {"en-US", "en-IN"}
    assert languages.available("post_op", channel="sms") == {"en-US", "en-IN"}
    assert languages.hold_reason("vi-VN", "post_op") == "Waiting for Vietnamese support"
    assert languages.wording_source("vi-VN", "post_op") is None
    assert languages.wording_source("es-US", "post_op") is None
    assert "source" not in languages.catalog("post_op")[0]
    assert lp.voice_pack_for_call("post_op", "es-US", environ={}) is None
    assert lp.sms_pack("post_op", "es-US") is None


def test_on_all_24_are_available_for_calls_and_texts_and_nobody_is_held(auto):
    for channel in ("voice", "sms"):
        assert languages.available("post_op", channel=channel) == set(languages.CODES)
        assert languages.available("pre_op", channel=channel) == set(languages.CODES)
    for code in languages.CODES:
        assert languages.hold_reason(code, "post_op") is None
        assert languages.text_block(code, "post_op") is None
    assert languages.entry_hold_reason({"language": "ko-KR", "campaign_id": "pre_op"}) is None
    # An unsupported value is still held.
    assert languages.hold_reason("zh", "post_op") == "Language 'zh' is not supported"
    assert languages.text_language("vi-VN", "post_op") == "vi-VN"


def test_on_the_source_of_each_language(auto):
    assert languages.wording_source("en-US", "post_op") is None
    # es-US: the draft pack has both voice and SMS wording for post-op.
    assert languages.wording_source("es-US", "post_op", "voice") == "draft_pack"
    assert languages.wording_source("es-US", "post_op", "sms") == "draft_pack"
    # pre_op's Spanish draft has voice wording only: its texts are machine.
    assert languages.wording_source("es-US", "pre_op", "voice") == "draft_pack"
    assert languages.wording_source("es-US", "pre_op", "sms") == "machine"
    assert languages.wording_source("vi-VN", "post_op", "voice") == "machine"
    assert languages.wording_source("vi-VN", "post_op", "sms") == "machine"
    src = languages.sources("post_op")
    assert src["en-US"] == {"voice": "approved", "sms": "approved"}
    assert src["es-US"] == {"voice": "draft_pack", "sms": "draft_pack"}
    assert src["vi-VN"] == {"voice": "machine", "sms": "machine"}
    rows = {r["code"]: r for r in languages.catalog("post_op")}
    assert rows["vi-VN"] == {
        "code": "vi-VN",
        "name": "Vietnamese",
        "native": "Tiếng Việt",
        "available": True,
        "source": "machine",
        "sms_source": "machine",
    }
    assert rows["en-IN"]["source"] == "approved"


def test_call_in_english_still_forces_english(auto):
    assert languages.call_wording_source("vi-VN", "post_op", call_in_english=True) is None
    assert languages.call_wording_source("vi-VN", "post_op") == "machine"
    assert lp.voice_pack_for_call("post_op", "es-US", call_in_english=True, environ={}) is None
    assert languages.text_language("vi-VN", "post_op", call_in_english=True) is None


def test_an_approved_pack_is_reported_approved(auto, tmp_path, monkeypatch):
    import yaml
    from voa_core.campaign import default_config_dir

    src = default_config_dir() / "campaigns" / "i18n" / "post_op.es-US.yaml"
    doc = yaml.safe_load(src.read_text())
    doc.update(
        status="approved",
        reviewed_by="Test Translator, CMI",
        approved_by="Test Approver",
        approved_at="2026-10-22",
    )
    d = tmp_path / "campaigns" / "i18n"
    d.mkdir(parents=True)
    (d / "post_op.es-US.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    monkeypatch.setattr(languages, "pack_dir", lambda config_dir=None: d)
    assert languages.wording_source("es-US", "post_op", "voice") == "approved"


def test_on_a_draft_pack_is_used_for_calls_and_texts(auto):
    pack = lp.voice_pack_for_call("post_op", "es-US", environ={})
    assert pack is not None and pack.status == "draft"
    assert lp.sms_pack("post_op", "es-US") is not None
    assert lp.voice_pack_for_call("post_op", "vi-VN", environ={}) is None


def test_localized_campaign_ignores_a_pack_for_machine_wording():
    from voa_core.campaign import load_campaign

    c = load_campaign("post_op")
    assert lp.localized_campaign(c, "es-US", source="machine") is c
    assert lp.localized_campaign(c, "es-US").scripts["voicemail"] != c.scripts["voicemail"]


# --------------------------------------------------------------------------- SMS wording


def test_a_machine_conversation_writes_english_with_the_dob_asked_day_first(auto):
    s = {"sms_language": "vi-VN", "campaign_id": "post_op"}
    assert machine_language(s) == "vi-VN"
    m = session_copy(s)
    assert isinstance(m, PackCopy) and not m.approved
    assert "DD/MM/YYYY" in m.ask_dob("Jordan Rivera")
    assert "DD/MM/YYYY" in m.dob_unreadable()
    # Everything else is the English wording, byte for byte.
    assert m.emergency() == ENGLISH_COPY.emergency()
    assert {k for k, v in MACHINE_SOURCE.items() if v != ENGLISH_COPY_TEXTS()[k]} == {
        "ask_dob",
        "dob_unreadable",
    }
    # Spanish uses its draft pack, not machine translation.
    assert machine_language({"sms_language": "es-US", "campaign_id": "post_op"}) is None


def ENGLISH_COPY_TEXTS():  # noqa: N802
    from voa_core.sms_copy import ENGLISH

    return ENGLISH


def test_off_a_conversation_in_another_language_is_never_machine():
    s = {"sms_language": "vi-VN", "campaign_id": "post_op"}
    assert machine_language(s) is None
    assert session_copy(s) is ENGLISH_COPY


# --------------------------------------------------------------------------- translate_outbound


def test_translate_outbound_translates_and_caches():
    t = FakeTranslator(out={("Reply STOP to opt out.", "vi-VN"): "Trả lời STOP để hủy."})
    out = asyncio.run(translate_outbound(t, "Reply STOP to opt out.", "vi-VN"))
    assert out.translated and out.text == "Trả lời STOP để hủy." and out.error is None
    again = asyncio.run(translate_outbound(t, "Reply STOP to opt out.", "vi-VN"))
    assert again.text == out.text and len(t.out_calls) == 1


@pytest.mark.parametrize(
    ("english", "bad", "why"),
    [
        ("Reply STOP to opt out.", "Trả lời DỪNG để hủy.", "STOP"),
        ("Call 911 now.", "Gọi cấp cứu ngay.", "911"),
        ("Reply C to cancel.", "Trả lời H để hủy.", '"C"'),
        ("Tap tel:911 to call 911.", "Gọi 911.", "tel:911"),
    ],
)
def test_a_dropped_literal_sends_the_english(english, bad, why):
    t = FakeTranslator(out={(english, "vi-VN"): bad})
    out = asyncio.run(translate_outbound(t, english, "vi-VN"))
    assert not out.translated and out.text == english and out.source == "english_fallback"
    assert why in out.error
    # Never cached: the next send tries again.
    assert (english, "vi-VN") not in translate._OUT_CACHE


def test_a_failed_translation_sends_the_english():
    t = FakeTranslator(fail_out=True)
    out = asyncio.run(translate_outbound(t, "How are you feeling?", "ko-KR"))
    assert out.text == "How are you feeling?" and out.error.startswith("translation failed")


def test_an_empty_translation_sends_the_english():
    t = FakeTranslator(out=lambda text, target: "  ")
    out = asyncio.run(translate_outbound(t, "How are you feeling?", "ko-KR"))
    assert out.text == "How are you feeling?" and not out.translated
