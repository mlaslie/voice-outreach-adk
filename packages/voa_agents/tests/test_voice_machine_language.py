"""A call in a language WITHOUT a language pack, with the demo auto-approve on
(docs/LANGUAGES-PLAN.md, "Language availability and approval"): the English
wording plus a block telling the model to speak only that language and to
translate each scripted line faithfully. English and pack calls are unchanged.
Fictional data only."""

from __future__ import annotations

from types import SimpleNamespace

from voa_agents.voice.agent import build_voice_agent
from voa_agents.voice.instruction import build_instruction, language_block
from voa_core import language_packs
from voa_core.campaign import load_campaign
from voa_core.config import load_config

PATIENT = {"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": False}
POST_OP = load_campaign("post_op")
ES = language_packs.localize(POST_OP, language_packs.load_pack("post_op", "es-US"))


def vietnamese() -> str:
    return build_instruction(
        POST_OP,
        org_name="Acme Health",
        patient=PATIENT,
        language="vi-VN",
        language_source="machine",
    )


def english() -> str:
    return build_instruction(POST_OP, org_name="Acme Health", patient=PATIENT)


def test_the_machine_block_speaks_only_the_language_and_translates_faithfully():
    text = vietnamese()
    for line in (
        "# Language: Vietnamese",
        "Speak only Vietnamese to the caller. RESPOND IN VIETNAMESE. "
        "YOU MUST RESPOND UNMISTAKABLY IN VIETNAMESE.",
        "The scripted lines and questions below are written in English. Translate each one "
        "faithfully",
        "Never say the English.",
        "never add, drop, soften or explain anything",
        "fever threshold",
        "911",
        "medications",
        "pass answers in English: yes, no, unknown, a number, better, same or worse",
        "Record free-text answers as a short English summary",
        "If the caller asks to speak English, switch to English",
        '"Needs an interpreter: speaks <the language>"',
    ):
        assert line in text, line
    assert text.index("# Language: Vietnamese") < text.index("# Speaking style")
    assert text.rstrip().endswith("Tool arguments are in English.")
    # The scripted lines stay English (the model translates them as it speaks).
    assert "Please hang up now and call nine one one" in text
    assert "Say the scripted lines exactly as written below" not in text


def test_english_is_unchanged():
    text = english()
    assert "# Language" not in text and "RESPOND IN" not in text
    assert (
        build_instruction(
            POST_OP, org_name="Acme Health", patient=PATIENT, language=None, language_source=None
        )
        == text
    )
    # The frame after the block is the English instruction, byte for byte.
    head, _, rest = text.partition("\n\n")
    assert vietnamese().startswith(head + "\n\n")
    assert rest in vietnamese()


def test_a_pack_call_is_unchanged_by_the_source():
    plain = build_instruction(ES, org_name="Acme Health", patient=PATIENT, language="es-US")
    for source in ("approved", "draft_pack"):
        assert (
            build_instruction(
                ES,
                org_name="Acme Health",
                patient=PATIENT,
                language="es-US",
                language_source=source,
            )
            == plain
        )
    assert language_block("es-US") in plain


def _agent_instruction(state: dict) -> str:
    agent = build_voice_agent(load_config(environ={"VOA__GCP__PROJECT_ID": "test-project"}))
    return agent.instruction(SimpleNamespace(state=state))


def test_the_agent_uses_the_machine_block_only_when_the_gateway_said_so():
    base = {"patient": PATIENT, "campaign_id": "post_op", "org_name": "Acme Health"}
    assert _agent_instruction(base) == english()
    assert (
        _agent_instruction({**base, "call_language": "vi-VN", "call_language_source": "machine"})
        == vietnamese()
    )
    # A machine call never picks up a pack's wording, even when a pack file exists.
    es_machine = _agent_instruction(
        {**base, "call_language": "es-US", "call_language_source": "machine"}
    )
    assert "Please hang up now and call nine one one" in es_machine
    assert "Por favor, cuelgue ahora" not in es_machine
