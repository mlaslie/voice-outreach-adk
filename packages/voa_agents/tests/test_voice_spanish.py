"""A call spoken in Spanish (docs/LANGUAGES-PLAN.md step 4): the instruction's
language block and the pack's wording, the closing in Spanish, the interpreter
path, and the per-call speech config. English is covered byte for byte by the
parity goldens; the checks here only show English did not pick anything up.
Fictional data only."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from google.adk.models import Gemini
from google.genai import types
from voa_agents.voice import tools
from voa_agents.voice.agent import PerCallSpeechGemini, build_voice_agent
from voa_agents.voice.instruction import build_instruction
from voa_core import language_packs
from voa_core.campaign import load_campaign
from voa_core.config import load_config

PATIENT = {"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": False}
POST_OP = load_campaign("post_op")
ES = language_packs.localize(POST_OP, language_packs.load_pack("post_op", "es-US"))


def spanish() -> str:
    return build_instruction(ES, org_name="Acme Health", patient=PATIENT, language="es-US")


def english() -> str:
    return build_instruction(POST_OP, org_name="Acme Health", patient=PATIENT)


def test_the_language_block_says_what_the_plan_asks():
    text = spanish()
    for line in (
        "Speak only Spanish (US) to the caller. RESPOND IN SPANISH. "
        "YOU MUST RESPOND UNMISTAKABLY IN SPANISH.",
        "Say the scripted lines exactly as written below",
        "pass answers in English: yes, no, unknown, a number, better, same or worse",
        "Record free-text answers as a short English summary of what the caller said",
        "If the caller asks to speak English, switch to English",
        "If they speak another language you cannot continue in",
        '"Needs an interpreter: speaks <the language>"',
        'use "usted"',
    ):
        assert line in text, line
    # Right after the opening paragraph, with a reminder at the very end.
    assert text.index("# Language: Spanish (US)") < text.index("# Speaking style")
    assert text.rstrip().endswith("Tool arguments are in English.")


def test_the_scripts_and_questions_are_the_packs():
    text = spanish()
    assert (
        '"Hola, esta es una llamada automatizada de Acme Health. ¿Podría hablar con '
        'Jordan Rivera, por favor?"' in text
    )
    assert "Por favor, cuelgue ahora y llame al nueve uno uno." in text
    assert '- pain_present: "¿Tiene algún dolor?" Record "yes" or "no".' in text
    assert "Hello, this is an automated call" not in text
    assert "Please hang up now and call nine one one" not in text
    # The frame (ids, outcomes, the red flags, the tools) is the English one.
    for same in (
        "chest pain, pressure, tightness or squeezing",
        '"questionnaire_completed"',
        "verify_identity",
        "record_answer with the question id",
    ):
        assert same in text


def test_english_is_unchanged():
    text = english()
    assert "# Language" not in text and "RESPOND IN" not in text
    assert (
        build_instruction(POST_OP, org_name="Acme Health", patient=PATIENT, language=None) == text
    )


def _agent_instruction(state: dict) -> str:
    agent = build_voice_agent(load_config(environ={"VOA__GCP__PROJECT_ID": "test-project"}))
    ctx = SimpleNamespace(state=state)
    return agent.instruction(ctx)


def test_the_agent_builds_spanish_only_when_the_gateway_set_the_call_language():
    base = {"patient": PATIENT, "campaign_id": "post_op", "org_name": "Acme Health"}
    assert _agent_instruction(base) == english()
    # patient_language alone (step 3: a Spanish patient called in English) is English.
    assert _agent_instruction({**base, "patient_language": "es-US"}) == english()
    assert _agent_instruction({**base, "call_language": "es-US"}) == spanish()
    # The knowledge base never reaches a Spanish call's instruction.
    kn = {**base, "call_language": "es-US", "knowledge": {"ask_satisfied": True}}
    assert _agent_instruction(kn) == spanish()


def test_the_agent_model_takes_a_per_call_speech_config():
    agent = build_voice_agent(load_config(environ={"VOA__GCP__PROJECT_ID": "test-project"}))
    assert isinstance(agent.model, PerCallSpeechGemini)
    assert agent.model.speech_config.language_code == "en-US"


async def test_per_call_speech_config_wins_only_when_set(monkeypatch):
    seen = []

    @contextlib.asynccontextmanager
    async def fake_connect(self, llm_request):
        seen.append(self.speech_config.language_code if self.speech_config else None)
        yield "conn"

    monkeypatch.setattr(Gemini, "connect", fake_connect)
    model = PerCallSpeechGemini(
        model="gemini-3.8-live",
        speech_config=types.SpeechConfig(language_code="en-US"),
    )

    def req(speech):
        return SimpleNamespace(live_connect_config=types.LiveConnectConfig(speech_config=speech))

    async with model.connect(req(None)) as c:
        assert c == "conn"
    async with model.connect(req(types.SpeechConfig(language_code="es-US"))) as c:
        assert c == "conn"
    assert seen == ["en-US", "es-US"]
    assert model.speech_config.language_code == "en-US"  # the shared model is untouched


# --------------------------------------------------------------------------- tools


def ctx(**state):
    return SimpleNamespace(
        state={"patient": {"dob": "1950-03-15", "patient_id": "TEST-0001"}, "answers": {}, **state}
    )


ALL_CLEAR = {
    "overall_feeling": "doing fine",
    "pain_present": "no",
    "fever_reported": "no",
    "incision_concerns": "no",
    "legs_swollen": "no",
    "sleeping_well": "yes",
    "has_questions": "no",
}


async def _complete(c):
    for qid, a in ALL_CLEAR.items():
        assert (await tools.record_answer(qid, a, c))["status"] == "recorded"
    return await tools.submit_report("questionnaire_completed", c)


async def test_the_closing_is_the_packs_on_a_spanish_call():
    r = await _complete(ctx(call_language="es-US", patient_language="es-US"))
    assert r["status"] == "ok"
    assert r["closing"].startswith("Antes de despedirme, por favor lleve una lista de todos los")
    assert r["closing"].endswith("No necesita hacer nada más hoy. Cuídese mucho, adiós.")


async def test_the_closing_is_english_otherwise():
    for c in (ctx(), ctx(patient_language="es-US")):
        r = await _complete(c)
        assert r["closing"].startswith("Before I let you go, please bring a list of your current")


async def test_answers_on_a_spanish_call_are_english_typed_values():
    c = ctx(call_language="es-US", patient_language="es-US")
    assert (await tools.record_answer("pain_present", "sí", c))["status"] == "unclear"
    assert (await tools.record_answer("pain_present", "yes", c))["status"] == "recorded"
    assert c.state["answers"]["pain_present"] == "yes"


async def test_a_caller_who_needs_an_interpreter_gets_a_nurse_call_back():
    c = ctx(call_language="es-US", patient_language="es-US")
    await tools.record_patient_question("Needs an interpreter: speaks Mandarin", c)
    r = await tools.submit_report("reschedule_requested", c)
    assert r == {"status": "ok"}
    rep = c.state["report"]
    assert rep["outcome"] == "reschedule_requested"
    assert rep["follow_up"] == "routine_next_business_day"
    assert rep["follow_up_reason"] == tools.LANGUAGE_ASSISTANCE_REASON


@pytest.mark.parametrize("state", [{}, {"patient_language": "es-US"}])
async def test_an_english_reschedule_is_unchanged(state):
    c = ctx(**state)
    await tools.record_patient_question("Needs an interpreter: speaks Mandarin", c)
    await tools.submit_report("reschedule_requested", c)
    assert c.state["report"]["follow_up"] == "none"


async def test_a_plain_spanish_reschedule_needs_no_nurse():
    c = ctx(call_language="es-US", patient_language="es-US")
    await tools.submit_report("reschedule_requested", c)
    assert c.state["report"]["follow_up"] == "none"


async def test_free_text_on_a_spanish_call_must_be_an_english_summary():
    c = ctx(call_language="es-US", patient_language="es-US")
    r = await tools.record_answer("overall_feeling", "feeling جيد", c)
    assert r["status"] == "unclear" and "short English summary" in r["message"]
    assert "overall_feeling" not in c.state["answers"]
    assert (await tools.record_answer("overall_feeling", "feeling good", c))["status"] == "recorded"
    # Step 3 behaviour unchanged where the call is not spoken in Spanish.
    c2 = ctx(patient_language="es-US")
    assert (await tools.record_answer("overall_feeling", "me siento bien", c2))[
        "status"
    ] == "recorded"
