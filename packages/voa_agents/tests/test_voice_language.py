"""Voice tools for a patient in another language (docs/LANGUAGES-PLAN.md §3.3,
step 3). The declarations are unchanged; only non-English values behave
differently. Fictional data only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from voa_agents.voice import knowledge as vk
from voa_agents.voice import tools
from voa_core.translate import FakeTranslator


def ctx(**state):
    return SimpleNamespace(state={"patient": {"dob": "1950-03-15"}, "answers": {}, **state})


@pytest.fixture(autouse=True)
def _no_translator():
    tools.set_dob_translator(None)
    yield
    tools.set_dob_translator(None)


# --------------------------------------------------------------------------- record_answer


@pytest.mark.parametrize(
    "qid,said",
    [
        ("pain_present", "no sé"),  # was "no"
        ("pain_present", "no se"),
        ("pain_present", "sí"),
        ("pain_trend", "sí"),  # was "same"
        ("pain_severity", "siete"),
        ("fever_reported", "Да"),
        ("legs_swollen", "tal vez"),
    ],
)
async def test_a_typed_answer_not_in_english_is_refused_not_parsed(qid, said):
    c = ctx()
    r = await tools.record_answer(qid, said, c)
    assert r["status"] == "unclear"
    assert "Record the answer in English, e.g. yes / no / unknown / a number" in r["message"]
    assert qid not in c.state["answers"]
    assert c.state["tool_audit"][-1]["status"] == "not_english"


async def test_english_answers_are_unchanged():
    c = ctx()
    for qid, said, want in [
        ("pain_present", "no", "no"),
        ("pain_present", "yes", "yes"),
        ("pain_severity", "about a nine", 9),
        ("pain_trend", "worse", "worse"),
    ]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
        assert c.state["answers"][qid] == want
    # free text keeps the patient's words in any language (the checks fail safe)
    assert (await tools.record_answer("pain_location", "la rodilla", c))["status"] == "recorded"
    assert c.state["answers"]["pain_location"] == "la rodilla"


def test_tool_declarations_did_not_change():
    import inspect

    assert inspect.signature(tools.record_answer).parameters.keys() == {
        "question_id",
        "answer",
        "tool_context",
    }
    assert 'Use "yes"/"no" for yes/no questions' in tools.record_answer.__doc__
    assert "language" not in tools.verify_identity.__doc__.lower()


# --------------------------------------------------------------------------- questions


async def test_a_question_in_another_language_is_kept_and_immediate():
    c = ctx(patient_language="es-US")
    r = await tools.record_patient_question("¿Qué dieta debo seguir si tengo fiebre?", c)
    assert r["status"] == "recorded"
    q = c.state["answers"]["patient_questions"][0]
    assert q["priority"] == "urgent_same_day" and q["category"] == "other"
    assert q["text_original"] == "¿Qué dieta debo seguir si tengo fiebre?"
    assert q["lang"] == "es-US"


async def test_an_english_question_is_unchanged():
    c = ctx()
    await tools.record_patient_question("When can I drive?", c)
    q = c.state["answers"]["patient_questions"][0]
    assert q == {"text": "When can I drive?", "category": "activity",
                 "priority": "routine_next_business_day"}  # fmt: skip


async def test_the_knowledge_base_defers_another_language():
    class Agent:
        called = False

        async def answer(self, *a, **k):
            Agent.called = True
            raise AssertionError("must not be asked")

    c = ctx()
    c.session = SimpleNamespace(id="s-lang")
    vk.register("s-lang", vk.KnowledgeRuntime(snapshot=None, settings={}, agent=Agent()))
    try:
        r = await vk.answer_from_knowledge("¿Cuándo me puedo duchar?", c)
    finally:
        vk.unregister("s-lang")
    assert r == {"outcome": "deferred"} and not Agent.called
    q = c.state["answers"]["patient_questions"][0]
    assert q["outcome"] == "deferred" and q["priority"] == "urgent_same_day"
    assert c.state["tool_audit"][-1]["reason"] == "language"


# --------------------------------------------------------------------------- verify_identity


async def test_day_first_for_a_patient_in_another_language():
    c = ctx(patient_language="es-US")
    assert (await tools.verify_identity("15/03/1950", c))["result"] == "match"
    # English patient: month first, as before ("15/03/1950" is no date).
    e = ctx()
    assert (await tools.verify_identity("15/03/1950", e))["result"] == "retry"


async def test_an_ambiguous_numeric_date_asks_once_for_the_month_name():
    c = ctx(patient_language="es-US", patient={"dob": "1950-05-03"})
    # 05/03 read day first is 5 March: not the record (3 May). Ask, don't guess.
    r = await tools.verify_identity("05/03/1950", c)
    assert r["result"] == "retry" and "month" in r["say"]
    assert c.state["dob_attempts"] == 0
    assert (await tools.verify_identity("3 May 1950", c))["result"] == "match"


async def test_a_spoken_date_in_another_language_is_translated():
    t = FakeTranslator({"15 de marzo de 1950": ("es", "March 15, 1950")})
    tools.set_dob_translator(t)
    c = ctx()
    assert (await tools.verify_identity("15 de marzo de 1950", c))["result"] == "match"
    assert t.calls == [["15 de marzo de 1950"]]
    t2 = FakeTranslator(
        {"cinco de marzo de mil novecientos cincuenta": ("es", "March fifth, nineteen fifty")}
    )
    tools.set_dob_translator(t2)
    c2 = ctx(patient={"dob": "1950-03-05"})
    r = await tools.verify_identity("cinco de marzo de mil novecientos cincuenta", c2)
    assert r["result"] == "match"


async def test_a_slow_translator_gives_up_quickly():
    class Slow:
        model = "slow"

        async def translate_turns(self, turns, *, target="en"):
            await asyncio.sleep(10)

    tools.set_dob_translator(Slow())
    tools_timeout = tools.DOB_TRANSLATE_TIMEOUT_S
    tools.DOB_TRANSLATE_TIMEOUT_S = 0.05
    try:
        r = await tools.verify_identity("quince de marzo", ctx())
    finally:
        tools.DOB_TRANSLATE_TIMEOUT_S = tools_timeout
    assert r["result"] == "retry"


async def test_unparseable_replies_are_bounded():
    # Before: every unparseable reply was free, so the call could loop forever.
    c = ctx()
    results = [(await tools.verify_identity("I'd rather not", c))["result"] for _ in range(4)]
    # 1st and 2nd: free; 3rd: a failed attempt; 4th: the second failed attempt.
    assert results == ["retry", "retry", "retry", "mismatch_final"]
    assert c.state["dob_verification_result"] == "mismatch"
    audit = [a["result"] for a in c.state["tool_audit"]]
    assert audit == ["retry_unparseable", "retry_unparseable", "unparseable_counted",
                     "mismatch_final"]  # fmt: skip


async def test_one_unparseable_then_a_match_still_matches():
    c = ctx()
    assert (await tools.verify_identity("hmm", c))["result"] == "retry"
    assert (await tools.verify_identity("June 2nd 1961", c))["result"] == "needs_readback"
    assert (await tools.verify_identity("March 15, 1950", c))["result"] == "match"
