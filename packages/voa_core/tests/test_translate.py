"""voa_core.translate: the heuristic, language tags, applying results, the fake."""

import asyncio

import pytest
from voa_core import translate as tr
from voa_core.tasks import MemoryTaskQueue


@pytest.mark.parametrize(
    "text",
    ["Yes", "OK", "7", "no", "I don't know", "My knee hurts a bit, maybe a 4.", "", "Hmm."],
)
def test_english_looks_english(text):
    assert tr.looks_english(text)


@pytest.mark.parametrize(
    "text",
    [
        "Sí",
        "Tengo dolor en la pierna",  # no accents, still Spanish
        "no tengo fiebre",
        "Não, obrigado",
        "Je suis fatigué",
        "ich habe Schmerzen",
        "Không, cảm ơn",
        "saya tidak tahu",
        "Нет",
        "我不知道",
        "لا",
        "नहीं",
    ],
)
def test_other_languages_do_not_look_english(text):
    assert not tr.looks_english(text)


def test_all_english_is_for_whole_batches():
    assert tr.all_english(["Hi", "Yes", "7"])
    assert not tr.all_english(["Hi", "Sí"])


@pytest.mark.parametrize(
    ("tag", "code"),
    [
        ("es", "es-US"),
        ("es-MX", "es-US"),
        ("en", "en-US"),
        ("en-GB", "en-US"),
        ("vi", "vi-VN"),
        ("ru-RU", "ru-RU"),
        ("zh", "zh"),  # not one of the 24: kept as given
        ("", "en-US"),
        (None, "en-US"),
    ],
)
def test_to_supported(tag, code):
    assert tr.to_supported(tag) == code


def test_majority_language():
    assert tr.majority_language([]) == "en-US"
    assert tr.majority_language(["es", "en", "es-US"]) == "es-US"
    assert tr.majority_language(["vi", "en"]) == "vi-VN"  # tie: first seen
    assert tr.majority_language([None, None]) == "en-US"


def test_apply_to_turns_keeps_the_original_and_checks_the_count():
    turns = [{"role": "patient", "text": "Sí", "at_ms": 1}]
    out = tr.apply_to_turns(turns, [tr.TurnTranslation("es", "Yes")])
    assert out == [{"role": "patient", "text": "Sí", "at_ms": 1, "lang": "es-US", "text_en": "Yes"}]
    assert turns[0] == {"role": "patient", "text": "Sí", "at_ms": 1}
    with pytest.raises(tr.TranslationFailed):
        tr.apply_to_turns(turns, [])


def test_spoken_languages_are_the_patients_in_order():
    turns = [
        {"role": "agent", "lang": "ru-RU"},
        {"role": "patient", "lang": "es-US"},
        {"role": "patient", "lang": "en-US"},
        {"role": "patient", "lang": "es-US"},
    ]
    assert tr.spoken_languages(turns) == ["es-US", "en-US"]


def test_the_fake():
    f = tr.FakeTranslator({"Sí": ("es", "Yes")})
    out = asyncio.run(f.translate_turns(["Sí", "OK"]))
    assert out == [tr.TurnTranslation("es", "Yes"), tr.TurnTranslation("en", None)]
    with pytest.raises(tr.TranslationFailed):
        asyncio.run(tr.FakeTranslator(fail=True).translate_turns(["x"]))


def test_enqueue_never_raises():
    q = MemoryTaskQueue()
    assert asyncio.run(tr.enqueue(q, "voa-events", call_id="CA1", name="xlate-CA1"))
    assert q.tasks[0]["path"] == "/tasks/transcript-translate"
    assert q.tasks[0]["payload"] == {"call_id": "CA1"}
    assert not asyncio.run(tr.enqueue(q, "voa-events", call_id="CA1", name="xlate-CA1"))

    class Broken:
        async def enqueue(self, *a, **k):
            raise RuntimeError("down")

    assert not asyncio.run(tr.enqueue(Broken(), "q", call_id="CA1"))
    assert not asyncio.run(tr.enqueue(q, "q", call_id=None))
