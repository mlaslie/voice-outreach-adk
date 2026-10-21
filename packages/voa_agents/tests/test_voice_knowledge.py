"""The knowledge agent on the voice agent: off and shadow = exactly today's agent;
live = the knowledge tools, backed by voa_knowledge.KnowledgeAgent (fakes here)."""

import json
from pathlib import Path
from types import SimpleNamespace

from voa_agents.voice import knowledge as vk
from voa_agents.voice import tools
from voa_agents.voice.agent import build_voice_agent
from voa_agents.voice.instruction import build_instruction
from voa_core import knowledge as kb
from voa_core.campaign import load_campaign
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_knowledge.testing import FixedClassifier, FixedGrounder, offline_agent

GOLDEN = Path(__file__).parent / "golden"
PATIENT = {"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": False}
KNOWLEDGE_WORDS = ("answer_from_knowledge", "record_answer_feedback", "Here's what the clinic says")

GUIDE = {
    "doc_id": "d-1",
    "title": "Lifting after outpatient knee arthroscopy",
    "body": "Lift nothing heavier than ten pounds for two weeks.",
    "procedures": ["knee arthroscopy"],
    "care_settings": ["outpatient"],
    "status": "approved",
    "version": 2,
}
SHOWER = {
    "item_id": "i-001",
    "question": "When can I shower?",
    "alt_questions": [],
    "answer": "You can usually shower two days after surgery.",
    "category": "activity",
    "status": "approved",
    "version": 2,
}


def cfg():
    return load_config(environ={"VOA_ENV": "test", "VOA__GCP__PROJECT_ID": "test-project"})


# --------------------------------------------------------------------------- off: unchanged


def test_instruction_is_unchanged_when_the_knowledge_base_is_off():
    c = load_campaign("post_op")
    for patient in (PATIENT, {**PATIENT, "is_diabetic": True}):
        today = build_instruction(c, org_name="Example Clinic", patient=patient)
        off = build_instruction(c, org_name="Example Clinic", patient=patient, knowledge=None)
        assert off == today
        assert not any(w in off for w in KNOWLEDGE_WORDS)
        assert "Never answer their questions, even if you know the answer" in off


def test_instruction_when_on_changes_only_the_questions_step():
    c = load_campaign("post_op")
    off = build_instruction(c, org_name="Example Clinic", patient=PATIENT).splitlines()
    on = build_instruction(
        c, org_name="Example Clinic", patient=PATIENT, knowledge={"ask_satisfied": True}
    ).splitlines()
    assert len(on) == len(off)
    changed = [(a, b) for a, b in zip(off, on, strict=True) if a != b]
    assert len(changed) == 1 and changed[0][1].startswith("- has_questions:")
    line = changed[0][1]
    assert "answer_from_knowledge" in line and "Here's what the clinic says:" in line
    assert '"Did that answer your question?"' in line and "Let me check that for you" in line
    no_ask = build_instruction(
        c, org_name="Example Clinic", patient=PATIENT, knowledge={"ask_satisfied": False}
    )
    assert "Did that answer your question?" not in no_ask
    # The emergency check and its script are untouched.
    safety = lambda lines: "\n".join(lines).split("# Safety")[1]  # noqa: E731
    assert safety(on) == safety(off)


def _decls(tool_list):
    return [t._get_declaration().model_dump(mode="json", exclude_none=True) for t in tool_list]


def test_tool_declarations_are_byte_for_byte_today():
    want = json.loads((GOLDEN / "tool_declarations.json").read_text())
    assert _decls(tools.build_tools()) == want


async def test_the_agent_exposes_the_knowledge_tools_only_when_switched_on():
    agent = build_voice_agent(cfg())
    want = json.loads((GOLDEN / "tool_declarations.json").read_text())

    async def names(state):
        ctx = SimpleNamespace(state=state, invocation_id="x", agent_name=agent.name)
        out = []
        for t in agent.tools:
            if hasattr(t, "get_tools"):
                out += await t.get_tools(ctx)
            else:
                out.append(t)
        return out

    off = await names({"campaign_id": "post_op"})
    assert _decls(off) == want
    on = await names({"campaign_id": "post_op", "knowledge": {"ask_satisfied": True}})
    assert [t.name for t in on][-2:] == ["answer_from_knowledge", "record_answer_feedback"]


async def test_prepare_is_off_by_default_and_in_shadow_and_on_only_when_live():
    store = MemoryStore()
    agent = offline_agent()
    assert await vk.prepare(store, cfg(), agent=agent) == (None, None)
    for doc in (
        {"mode": "shadow"},  # shadow: the call is exactly as today
        {"mode": "live", "channels": {"voice": False}},
        {"enabled": False},  # a v1 document
    ):
        await store.set("settings", "knowledge", doc)
        kb.forget(store)
        assert await vk.prepare(store, cfg(), agent=agent) == (None, None), doc
    await store.set("settings", "knowledge", {"mode": "live", "ask_satisfied": False})
    await store.set("knowledge_items", "i-001", SHOWER)
    await store.set("knowledge_documents", "d-1", GUIDE)
    kb.forget(store)
    flag, rt = await vk.prepare(store, cfg(), agent=agent)
    assert flag == {"ask_satisfied": False} and [i["item_id"] for i in rt.items] == ["i-001"]
    assert [d["doc_id"] for d in rt.snapshot.guidelines] == ["d-1"] and rt.agent is agent
    # a v1 "enabled: true" reads as live
    await store.set("settings", "knowledge", {"enabled": True})
    kb.forget(store)
    assert (await vk.prepare(store, cfg(), agent=agent))[0] == {"ask_satisfied": True}


# --------------------------------------------------------------------------- on: the tools


PATIENT_STATE = {
    "first_name": "Jordan",
    "last_name": "Rivera",
    "patient_id": "A1234567",
    "dob": "1950-03-15",
    "operation_name": "Knee arthroscopy",
    "operation_date": "2026-10-16",
    "care_setting": "outpatient",
    "is_diabetic": False,
}


def ctx(session_id="s-1", **state):
    return SimpleNamespace(
        state={"patient": dict(PATIENT_STATE), "answers": {}, **state},
        session=SimpleNamespace(id=session_id),
    )


class Spy:
    """Wraps an agent and records what it was asked with."""

    def __init__(self, agent):
        self.agent, self.calls = agent, []

    async def answer(self, question, context=None, **kw):
        self.calls.append((question, dict(context or {})))
        return await self.agent.answer(question, context, **kw)


def on(session_id="s-1", agent=None, snapshot=None):
    rt = vk.KnowledgeRuntime(
        snapshot or kb.KnowledgeSnapshot(examples=[SHOWER], guidelines=[GUIDE]),
        kb.normalize_settings({"mode": "live"}),
        agent or offline_agent(),
    )
    vk.register(session_id, rt)
    return rt


async def test_an_answered_question_is_recorded_with_its_sources_and_score():
    spy = Spy(offline_agent(grounder=FixedGrounder(0.92)))
    on(agent=spy)
    c = ctx()
    try:
        r = await vk.answer_from_knowledge("when can I shower", c)
    finally:
        vk.unregister("s-1")
    assert r == {
        "outcome": "answered",
        "answer": "You can usually shower two days after surgery.",
        "source": "the clinic's answer",
    }
    (q,) = c.state["answers"]["patient_questions"]
    assert q["outcome"] == "answered" and q["answer"] == SHOWER["answer"]
    assert q["sources"] == [
        {"kind": "example", "id": "i-001", "title": "When can I shower?", "version": 2}
    ]
    assert q["support_score"] == 0.92 and q["satisfied"] is None
    assert c.state["answers"]["has_questions"] == "yes"
    audit = c.state["tool_audit"][-1]
    assert audit["outcome"] == "answered" and "shower" not in json.dumps(audit)
    # The agent got the patient context, never an identifier.
    ((question, context),) = spy.calls
    assert question == "when can I shower"
    assert set(context) == {"procedure", "days_since_surgery", "care_setting", "is_diabetic"}
    assert context["procedure"] == "Knee arthroscopy" and context["care_setting"] == "outpatient"
    assert "Jordan" not in json.dumps(context) and "1950" not in json.dumps(context)


async def test_a_question_deferred_by_the_llm_is_triaged_at_its_stricter_priority():
    # The keywords call it recovery (next day); the LLM says symptom: immediate.
    on(agent=offline_agent(classifier=FixedClassifier("symptom")))
    c = ctx()
    try:
        r = await vk.answer_from_knowledge("is it normal to feel this tired", c)
    finally:
        vk.unregister("s-1")
    assert r == {"outcome": "deferred"}
    (q,) = c.state["answers"]["patient_questions"]
    assert (q["category"], q["priority"], q["outcome"]) == (
        "symptom",
        "urgent_same_day",
        "deferred",
    )


async def test_an_agent_that_raises_defers():
    class Broken:
        async def answer(self, *a, **kw):
            raise RuntimeError("down")

    on(agent=Broken())
    c = ctx()
    try:
        r = await vk.answer_from_knowledge("when can I shower", c)
    finally:
        vk.unregister("s-1")
    assert r == {"outcome": "deferred"}
    assert c.state["answers"]["patient_questions"][0]["outcome"] == "deferred"


async def test_a_medication_question_is_deferred_and_recorded():
    on()
    c = ctx()
    try:
        r = await vk.answer_from_knowledge("how much tylenol can I take", c)
    finally:
        vk.unregister("s-1")
    assert r == {"outcome": "deferred"}
    (q,) = c.state["answers"]["patient_questions"]
    assert q["outcome"] == "deferred" and q["priority"] == "urgent_same_day" and "answer" not in q
    # The model also calling the old tool does not record it twice.
    assert (await tools.record_patient_question("how much tylenol can I take", c))["count"] == 1
    assert len(c.state["answers"]["patient_questions"]) == 1


async def test_a_red_flag_question_points_back_to_the_safety_rule():
    on()
    c = ctx()
    try:
        r = await vk.answer_from_knowledge("I have chest pain, can I shower", c)
    finally:
        vk.unregister("s-1")
    assert r["outcome"] == "deferred" and "Safety" in r["safety"]


async def test_no_runtime_defers():
    r = await vk.answer_from_knowledge("when can I shower", ctx(session_id="nobody"))
    assert r == {"outcome": "deferred"}


async def _complete(c):
    for qid, said in [
        ("overall_feeling", "good"),
        ("pain_present", "no"),
        ("fever_reported", "no"),
        ("incision_concerns", "no"),
        ("legs_swollen", "no"),
        ("sleeping_well", "yes"),
        ("has_questions", "yes"),
    ]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
    return await tools.submit_report("questionnaire_completed", c)


async def test_answered_and_satisfied_needs_no_follow_up():
    on()
    c = ctx(campaign_id="post_op")
    try:
        await vk.answer_from_knowledge("when can I shower", c)
        assert (await vk.record_answer_feedback("yes", c))["status"] == "recorded"
        r = await _complete(c)
    finally:
        vk.unregister("s-1")
    assert c.state["answers"]["patient_questions"][0]["satisfied"] is True
    assert c.state["report"]["follow_up"] == "none"
    assert "care team will follow up" not in r["closing"]
    assert r["closing"].startswith("Before I let you go")  # medication-list reminder kept


async def test_no_to_did_that_answer_it_becomes_a_nurse_follow_up():
    on()
    c = ctx(campaign_id="post_op")
    try:
        await vk.answer_from_knowledge("when can I shower", c)
        fb = await vk.record_answer_feedback("no", c)
        r = await _complete(c)
    finally:
        vk.unregister("s-1")
    assert "follow up" in fb["say"]
    (q,) = c.state["answers"]["patient_questions"]
    assert (
        q["outcome"] == "deferred" and q["satisfied"] is False and q["sources"][0]["version"] == 2
    )
    assert c.state["report"]["follow_up"] == "routine_next_business_day"
    assert "question asked (activity)" in c.state["report"]["follow_up_reason"]
    assert "care team will follow up" in r["closing"]
    assert (await vk.record_answer_feedback("no", c))["status"] == "error"  # nothing pending


def test_knowledge_tools_are_blocking():
    from google.genai import types

    assert all(t.behavior == types.Behavior.BLOCKING for t in vk.build_knowledge_tools())


async def test_prepare_warms_the_agent_in_the_background():
    import asyncio

    class Warmable:
        warmed = 0

        async def warm(self):
            Warmable.warmed += 1

        async def answer(self, *a, **kw):  # pragma: no cover - not called
            raise AssertionError

    store = MemoryStore()
    await store.set("settings", "knowledge", {"mode": "live"})
    kb.forget(store)
    state, runtime = await vk.prepare(store, cfg=None, agent=Warmable())
    assert state is not None and runtime is not None
    await asyncio.sleep(0)
    assert Warmable.warmed == 1
