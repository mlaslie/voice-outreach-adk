"""The voice agent on a second use case (pre-op): instruction, parsing, triage and
closing all come from the spec; the call frame and safety rules do not change."""

from types import SimpleNamespace

from voa_agents.voice import tools
from voa_agents.voice.instruction import build_instruction
from voa_core.campaign import SHARED_RED_FLAGS, load_campaign

PATIENT = {"first_name": "Casey", "last_name": "Morgan", "is_diabetic": True}


def ctx(**state):
    return SimpleNamespace(
        state={"campaign_id": "pre_op", "patient": {"dob": "1970-01-02", **PATIENT}, **state}
    )


def test_the_pre_op_instruction_has_its_questions_and_the_shared_frame():
    pre = build_instruction(load_campaign("pre_op"), org_name="Example Clinic", patient=PATIENT)
    post = build_instruction(load_campaign("post_op"), org_name="Example Clinic", patient=PATIENT)
    assert "- arrival_confirmed:" in pre and "- ride_home:" in pre and "pain_present" not in pre
    assert "medication-list reminder" not in pre and "medication-list reminder" in post
    assert all(f in pre for f in SHARED_RED_FLAGS)
    # Everything before the questions and after them is the same frame.
    assert pre.split("# Safety")[1] == post.split("# Safety")[1]
    # ... and so is the frame before them, apart from the consent script.
    c_pre, c_post = (load_campaign(i).scripts["consent"] for i in ("pre_op", "post_op"))
    head = lambda text, consent: text.split("4. QUESTIONS")[0].replace(  # noqa: E731
        consent.format(org_name="Example Clinic"), "<consent>"
    )
    assert head(pre, c_pre) == head(post, c_post)
    assert "Never answer their questions" in pre


async def test_pre_op_answers_are_parsed_by_type_and_triaged_by_its_rules():
    c = ctx(answers={})
    assert (await tools.record_answer("overall_feeling", "fine", c))["status"] == "error"
    assert (await tools.record_answer("arrival_confirmed", "hmm", c))["status"] == "unclear"
    for qid, said in [
        ("arrival_confirmed", "yes that works"),
        ("fasting_understood", "yes"),
        ("ride_home", "no, nobody"),
        ("stop_meds_instructions", "I don't remember"),
        ("feeling_unwell", "no"),
        ("has_questions", "no"),
    ]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
    a = c.state["answers"]
    assert a["ride_home"] == "no" and a["stop_meds_instructions"] == "unknown"
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok"
    report = c.state["report"]
    assert report["follow_up"] == "urgent_same_day"
    assert [f["text"] for f in report["follow_up_reasons"]] == [
        "unsure which medicines to stop",
        "no ride home arranged",
    ]
    # No closing_extra in pre-op: the closing is the goodbye alone.
    assert r["closing"] == load_campaign("pre_op").scripts["closing_follow_up"]


async def test_pre_op_needs_its_follow_up_before_it_is_complete():
    c = ctx(answers={})
    for qid, said in [
        ("arrival_confirmed", "yes"),
        ("fasting_understood", "yes"),
        ("ride_home", "yes"),
        ("stop_meds_instructions", "yes"),
        ("feeling_unwell", "yes"),
        ("has_questions", "no"),
    ]:
        await tools.record_answer(qid, said, c)
    r = await tools.submit_report("questionnaire_completed", c)
    assert r == {"status": "incomplete", "ask_these_questions": ["unwell_description"]}
    await tools.record_answer("unwell_description", "a runny nose since Friday", c)
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok" and c.state["report"]["follow_up"] == "urgent_same_day"


async def test_a_question_recorded_in_pre_op_marks_its_questions_answer():
    c = ctx(answers={})
    await tools.record_patient_question("can I take my blood thinner", c)
    assert c.state["answers"]["has_questions"] == "yes"
