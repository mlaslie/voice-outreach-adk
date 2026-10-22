"""Transcript cleaning on English calls (docs/LANGUAGES-PLAN.md step 2).

English speech must give exactly the transcript it always did; a turn spoken
wholly in another script is kept and counts as the caller speaking (before,
it became "" and ``submit_report`` refused to file the call)."""

import asyncio
import re
import unicodedata
from types import SimpleNamespace

import pytest
from voa_agents.voice.call_facts import CallFacts
from voa_gateway.session import clean_transcript

_MARKUP = re.compile(r"<[^>]{1,20}>|\{[a-z_]{1,20}\}")


def _before(text: str) -> str:
    """clean_transcript(latin_only=True) as it was before step 2 (a53146a)."""
    text = _MARKUP.sub(" ", text)
    text = "".join(
        ch
        for ch in text
        if not unicodedata.category(ch).startswith(("L", "M"))
        or unicodedata.name(ch, "").startswith("LATIN")
        or 0x300 <= ord(ch) <= 0x36F
    )
    return " ".join(text.split())


ENGLISH = [
    "Yes, that's right.",
    "My date of birth is March 15th, 1950.",
    "date of birth?িতার",  # the real-call garbage the filter was written for
    "I'd say about a 4 out of 10 <no speech>{pause}",
    "Ça va, José — 3/15/1950",
    "Café",
    "Café",  # decomposed accent
    "No fever. 98.6°F, I think?",
    "  lots   of\tspace  ",
    "OK",
    "7",
    "",
    "<noise>",
    "Hello there?Привет",  # Latin majority: the stray Cyrillic goes, as before
    "The pain is better, 谢谢",  # mostly English: as before
    "I don't know, maybe 3 days ago ☺",
]


@pytest.mark.parametrize("text", ENGLISH)
def test_english_turns_are_cleaned_exactly_as_before(text):
    assert clean_transcript(text, latin_only=True) == _before(text)


def test_the_original_garbage_case_is_still_dropped():
    assert clean_transcript("date of birth?িতার", latin_only=True) == "date of birth?"


@pytest.mark.parametrize(
    "text",
    [
        "我不知道，可能是三天前？",  # Chinese
        "Нет, температуры нет. Боль примерно 4 из 10.",  # Russian
        "لا، ليس لدي حمى. الألم ٤ من ١٠؟",  # Arabic
        "हाँ, मुझे थोड़ा दर्द है।",  # Hindi
        "はい、そうです。",  # Japanese
        "네, 괜찮아요",  # Korean
    ],
)
def test_turns_wholly_in_another_script_are_kept(text):
    # The bug: every letter vanished (only punctuation or digits were left).
    assert not any(unicodedata.category(c).startswith("L") for c in _before(text))
    assert clean_transcript(text, latin_only=True) == " ".join(text.split())


def test_a_turn_mostly_in_another_script_is_kept_whole():
    text = "OK, я не знаю"
    assert clean_transcript(text, latin_only=True) == text


def test_markup_is_still_stripped_from_non_latin_turns():
    assert clean_transcript("<no speech>Да, да{pause}", latin_only=True) == "Да, да"


def test_non_english_calls_keep_every_script():
    stray = "date of birth?িতার"
    assert clean_transcript(stray) == stray


# --------------------------------------------------------------------------- the gateway


def _session():
    from voa_core.config import load_config
    from voa_gateway.session import CallSession
    from voa_providers.audio import Codec
    from voa_providers.fake import FakeMediaSession

    cfg = load_config(environ={"VOA_ENV": "test", "VOA__GCP__PROJECT_ID": "test-project"})
    media = FakeMediaSession(codec=Codec.MULAW_8K, call_id="local_" + "a" * 16)
    s = CallSession(cfg=cfg, runner=None, media=media, sink=None, record=False)
    assert s._latin_only  # an English call
    return s


def _event(*, said=None, heard=None, finished=True):
    tr = lambda t: SimpleNamespace(text=t, finished=finished) if t else None  # noqa: E731
    return SimpleNamespace(
        get_function_calls=lambda: [],
        get_function_responses=lambda: [],
        input_transcription=tr(said),
        output_transcription=tr(heard),
        interrupted=False,
        turn_complete=False,
        content=None,
        error_code=None,
    )


@pytest.mark.parametrize(
    "said", ["我不知道", "Не знаю", "لا أعرف", "मुझे नहीं पता"], ids=["zh", "ru", "ar", "hi"]
)
def test_caller_turns_count_non_latin_speech(said):
    s = _session()

    async def go():
        await s._handle(_event(said=said))

    asyncio.run(go())
    assert s.facts.caller_turns == 1
    assert s.transcript[-1]["role"] == "patient" and s.transcript[-1]["text"] == said


def test_garbage_only_turn_still_does_not_count():
    s = _session()
    asyncio.run(s._handle(_event(said="<no speech>")))
    assert s.facts.caller_turns == 0 and s.transcript == []


@pytest.mark.parametrize("q", ["¿Tiene fiebre?", "你发烧吗？", "هل لديك حمى؟"])
def test_unanswered_question_sees_every_question_mark(q):
    f = CallFacts()
    f.agent_said(q)
    assert f.unanswered_question
    f.caller_spoke()
    assert not f.unanswered_question


def test_a_statement_is_not_an_unanswered_question():
    f = CallFacts()
    f.agent_said("谢谢。Thank you.")
    assert not f.unanswered_question
