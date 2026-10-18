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
    ]:
        assert (await tools.record_answer(qid, said, c))["status"] == "recorded"
    assert c.state["answers"]["pain_severity"] == 9
    r = await tools.submit_report("questionnaire_completed", c)
    assert r["status"] == "ok" and "nurses" in r["closing"]
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
