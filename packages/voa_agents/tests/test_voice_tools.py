from types import SimpleNamespace

from voa_agents.voice import tools


def ctx(**state):
    return SimpleNamespace(state={"patient": {"dob": "1950-03-15"}, **state})


async def test_identity_never_reveals_dob_and_limits_attempts():
    c = ctx()
    r1 = await tools.verify_identity("June 2nd 1961", c)
    assert r1["result"] == "needs_readback" and "1950" not in str(r1)
    r2 = await tools.verify_identity("June 2nd 1961", c)
    assert r2 == {"result": "mismatch_final"}
    assert c.state["dob_verification_result"] == "mismatch"


async def test_unclear_dob_costs_no_attempt():
    c = ctx()
    assert (await tools.verify_identity("I'd rather not", c))["result"] == "retry"
    assert (await tools.verify_identity("March 15, 1950", c))["result"] == "match"


async def test_submit_requires_answers_and_end_requires_submit():
    c = ctx(answers={})
    for qid, said in [("overall_feeling", "ok I guess"), ("pain_present", "yeah it hurts")]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
    assert c.state["answers"]["pain_present"] == "yes"  # normalized by the shared parser
    assert (await tools.end_call("done", c))["status"] == "refused"
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "incomplete"
    assert set(r["ask_these_questions"]) >= {"pain_severity", "fever_reported"}
    assert (await tools.record_answer("pain_severity", "hmm", c))["status"] == "unclear"
    for qid, said in [
        ("pain_severity", "about a nine"),
        ("pain_location", "my knee"),
        ("pain_trend", "about the same"),
        ("fever_reported", "no"),
        ("incision_concerns", "no it looks fine"),
        ("legs_swollen", "no"),
        ("sleeping_well", "pretty well"),
        ("has_questions", "no, that's all"),
    ]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
    assert c.state["answers"]["pain_severity"] == 9
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok" and "nurses" in r["closing"]
    assert r["closing"].startswith("Before I let you go, please bring a list of your current")
    assert c.state["report"]["follow_up"] == "urgent_same_day"
    assert "_raw" not in c.state["report"]["survey_responses"]
    assert (await tools.end_call("done", c))["status"] == "ending"


def test_all_tools_are_blocking():
    from google.genai import types

    assert all(t.behavior == types.Behavior.BLOCKING for t in tools.build_tools())


async def test_exit_refused_while_question_unanswered():
    from voa_agents.voice import call_facts

    c = ctx()
    c.session = SimpleNamespace(id="s-guard")
    facts = call_facts.register("s-guard")
    try:
        facts.caller_spoke()
        facts.agent_said("Could you tell me your date of birth? Thank you, goodbye.")
        r = await tools.submit_report("unable_to_reach_wrong_person", c)
        assert r["status"] == "refused" and "report" not in c.state
        # Emergencies are never blocked.
        assert (await tools.submit_report("emergency_ended", c))["status"] == "ok"
        facts.caller_spoke()
        assert (await tools.submit_report("unable_to_reach_wrong_person", c))["status"] == "ok"
    finally:
        call_facts.unregister("s-guard")


async def test_exits_wait_while_amd_pending_and_identity_flagged():
    from voa_agents.voice import call_facts

    c = ctx()
    c.session = SimpleNamespace(id="s-amd")
    facts = call_facts.register("s-amd")
    try:
        facts.amd_pending = True
        facts.caller_spoke()  # a voicemail greeting looks like a caller turn
        r = await tools.submit_report("unable_to_reach_wrong_person", c)
        assert r["status"] == "refused" and "connecting" in r["message"]
        assert (await tools.submit_report("emergency_ended", c))["status"] == "ok"
        assert (await tools.verify_identity("March 15, 1950", c))["result"] == "match"
        assert facts.identity_verified is True
    finally:
        call_facts.unregister("s-amd")


COMPLETE = [
    ("overall_feeling", "good"),
    ("pain_present", "no"),
    ("fever_reported", "no"),
    ("incision_concerns", "no"),
    ("legs_swollen", "no"),
    ("sleeping_well", "yes"),
]


async def _answer_all(c, extra=()):
    for qid, said in [*COMPLETE, *extra]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded", qid


async def test_patient_questions_are_recorded_classified_and_drive_the_closing():
    c = ctx(answers={})
    await _answer_all(c, [("has_questions", "yes")])
    r = await tools.record_patient_question("how much Tylenol can I take", c)
    assert r == {"status": "recorded", "count": 1}
    await tools.record_patient_question("when can I drive", c)
    qs = c.state["answers"]["patient_questions"]
    assert [(q["category"], q["priority"]) for q in qs] == [
        ("medication", "urgent_same_day"),
        ("activity", "routine_next_business_day"),
    ]
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok"
    assert "medications to your post-op follow-up clinic visit" in r["closing"]
    assert "follow up with you to get them answered" in r["closing"]
    report = c.state["report"]
    assert report["follow_up"] == "urgent_same_day"
    assert "question asked (medication)" in report["follow_up_reason"]
    assert len(report["survey_responses"]["patient_questions"]) == 2


async def test_question_limit():
    c = ctx(answers={})
    for i in range(5):
        assert (await tools.record_patient_question(f"can I shower {i}", c))["status"] == "recorded"
    assert (await tools.record_patient_question("one more", c))["status"] == "limit"
    assert len(c.state["answers"]["patient_questions"]) == 5


async def test_no_questions_and_no_findings_gets_the_all_clear_with_the_reminder():
    c = ctx(answers={})
    await _answer_all(c, [("has_questions", "no")])
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["closing"].startswith("Before I let you go")
    assert "Nothing else is required" in r["closing"]


async def test_blood_sugar_keeps_the_reading_and_unclear_is_unknown():
    c = ctx(answers={})
    await tools.record_answer("blood_sugar_status", "between 120 and 250", c)
    a = c.state["answers"]
    assert a["blood_sugar_status"] == "high" and a["blood_sugar_reading"] == "between 120 and 250"
    await tools.record_answer("blood_sugar_status", "I don't check it", c)
    assert c.state["answers"]["blood_sugar_status"] == "unknown"


async def test_swelling_needs_a_description_and_one_leg_is_same_day():
    c = ctx(answers={})
    await _answer_all(c, [("legs_swollen", "yes"), ("has_questions", "no")])
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "incomplete" and "swelling_description" in r["ask_these_questions"]
    await tools.record_answer("swelling_description", "just my left leg", c)
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok" and c.state["report"]["follow_up"] == "urgent_same_day"
