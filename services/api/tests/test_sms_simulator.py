"""SMS simulator (/api/sms/test/*). Fictional data only; nothing is sent.

The simulator runs whatever ``sms.dry_run`` says: by default these tests run it
on a service that sends REAL texts (``dry_run`` off, live provider "twilio").
The live provider is :class:`NeverTwilio`, which fails the test if anything
reaches it; the simulator's own provider is wrapped to count its sends.

Isolation is checked on the store itself: after a simulator conversation the
only real collection written is the audit log; everything else is under
``simulator__``.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from voa_api import sms_service as sms
from voa_api import sms_simulator
from voa_api.app import create_app
from voa_api.context import Api
from voa_core import survey
from voa_core.config import load_config
from voa_core.sms import states as st
from voa_core.store import MemoryStore, new_id
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice
from voa_providers.sms import DryRunSms, FakeSms

NOW = datetime(2026, 3, 4, 3, 30, tzinfo=UTC)  # 22:30 in New York: quiet hours
DAY = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # 10:00 in New York
PHONE = "+12025550101"
STRANGER = "+12025550177"
USER = {"X-Acting-User": "admin@example.org"}
REAL_CALL = "CA" + "7" * 32
NS = sms_simulator.NAMESPACE


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


class CountingDryRun(DryRunSms):
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, to, body):
        self.sent.append((to, body))
        return await super().send(to, body)


class NeverTwilio:
    """The live provider of a service that sends real texts. A simulator text
    reaching it fails the test (pytest.fail is not an Exception: send_sms cannot
    swallow it)."""

    name = "twilio"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, to, body):
        self.sent.append((to, body))
        pytest.fail("a simulator text reached the live SMS provider")


def make(dry_run=False, survey_enabled=True, provider=None, enabled=True, at=NOW):
    environ = {
        "VOA_ENV": "test",
        "VOA__GCP__PROJECT_ID": "test-project",
        "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
        "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
        "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
        "VOA__DIALING__ALLOWLIST": PHONE,
        "VOA__SITE__ORG_NAME": "Acme Health",
        "VOA__SMS__ENABLED": str(enabled).lower(),
        "VOA__SMS__SURVEY_ENABLED": str(survey_enabled).lower(),
        "VOA__SMS__DRY_RUN": str(dry_run).lower(),
    }
    cfg = load_config(environ=environ)
    api = Api(
        cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=Clock(at)
    )
    api.sms = provider if provider is not None else NeverTwilio()
    api.sms_interpreter = None
    sim(api).sms = CountingDryRun()
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api


def sim(api) -> Api:
    return sms_simulator.simulator_api(api)


def run(coro):
    return asyncio.run(coro)


def start(client, phone=PHONE, **kw):
    return client.post("/api/sms/test/start", json={"phone": phone, **kw}, headers=USER)


def reply(client, body, phone=PHONE):
    return client.post("/api/sms/test/reply", json={"phone": phone, "body": body}, headers=USER)


def outbound(res):
    return [m["body"] for m in res.json()["new_messages"]]


def sim_session(api, phone=PHONE):
    return run(sim(api).store.get("sms_sessions", phone))


def real_session(api, phone=PHONE):
    return run(api.store.get("sms_sessions", phone))


def written(api) -> set[str]:
    """Every collection holding at least one document (MemoryStore internals)."""
    return {coll for coll, docs in api.store._data.items() if docs}


def assert_no_real_writes(api):
    real = {c for c in written(api) if not c.startswith(NS)}
    assert real <= {"audit_log"}, real
    assert api.tasks.tasks == []  # nothing queued on the real queue
    assert api.sms.sent == []  # nothing reached the live provider
    assert api.voice.placed == []


# --------------------------------------------------------------------------- refusals


def test_refused_when_sms_is_disabled():
    client, api = make(enabled=False)
    r = start(client)
    assert r.status_code == 409 and "disabled" in r.json()["detail"]
    assert reply(client, "3").status_code == 409
    assert written(api) == set()


def test_refused_for_a_number_off_the_allowlist_or_not_e164():
    client, api = make()
    r = start(client, phone=STRANGER)
    assert r.status_code == 403
    assert start(client, phone="555-0101").status_code == 400
    assert reply(client, "3", phone=STRANGER).status_code == 403
    assert sim(api).sms.sent == [] and written(api) == set()


def test_refused_if_the_simulator_provider_is_not_dry_run():
    # Never expected (simulator_api installs DryRunSms), checked anyway.
    client, api = make()
    sim(api).sms = FakeSms()
    assert start(client).status_code == 500
    assert sim(api).sms.sent == [] and api.sms.sent == []


def test_simulator_store_namespaces_everything_and_never_writes_real_config():
    store = MemoryStore()
    view = sms_simulator.SimulatorStore(store)
    run(store.set("outbound_allowlist", PHONE, {"label": "real"}))
    run(store.set("sms_sessions", PHONE, {"state": "real"}))
    assert run(view.get("outbound_allowlist", PHONE)) == {"label": "real"}  # read-through
    assert run(view.get("sms_sessions", PHONE)) is None  # not the real session
    run(view.set("sms_sessions", PHONE, {"state": "sim"}))
    assert run(store.get("sms_sessions", PHONE)) == {"state": "real"}
    stored = run(store.get(NS + "sms_sessions", PHONE))
    assert stored["state"] == "sim" and "expire_at" in stored  # test_sms_simulator_expiry.py
    for coll in sms_simulator.READ_THROUGH:
        with pytest.raises(sms_simulator.SimulatorWriteRefused):
            run(view.delete(coll, PHONE))
        with pytest.raises(sms_simulator.SimulatorWriteRefused):
            run(view.merge(coll, PHONE, {"x": 1}))
        with pytest.raises(sms_simulator.SimulatorWriteRefused):
            run(view.transact(coll, PHONE, lambda cur: {"x": 1}))
    assert run(store.get("outbound_allowlist", PHONE)) == {"label": "real"}


def test_the_simulator_api_is_dry_run_whatever_the_service_says():
    _, api = make(dry_run=False)
    s = sim(api)
    assert api.cfg.sms.dry_run is False and s.cfg.sms.dry_run is True
    assert s.voice is None and isinstance(s.tasks, sms_simulator.DroppedTasks)
    assert isinstance(s.store, sms_simulator.SimulatorStore) and s.store.inner is api.store
    assert sim(api) is s  # built once per Api
    assert sms.provider(api) is api.sms and sms.provider(s).name == "dry_run"


# --------------------------------------------------------------------------- the full run


def answer_current(api, asked_question: list[bool]) -> str:
    """Answer whatever the session is on: clean except one medication question."""
    s = sim_session(api)
    pending = s.get("survey_pending") or []
    field = pending[0] if pending else s["survey_plan"][s["survey_index"]]
    if field == survey.F_PATIENT_QUESTIONS:
        if asked_question[0]:
            return "no"
        asked_question[0] = True
        return "How often should I take my pain pills"
    return {
        survey.Q_OVERALL: "pretty good",
        survey.Q_SLEEP: "yes",
        survey.Q_SUGAR: "120",
        survey.Q_HAS_QUESTIONS: "yes",
    }.get(field, "no")


@pytest.mark.parametrize("dry_run", [False, True])
def test_full_text_run_files_a_report_and_nothing_reaches_twilio_or_real_records(dry_run):
    client, api = make(dry_run=dry_run)
    r = start(client, diabetic=True)
    assert r.status_code == 200, r.text
    out = r.json()
    call_id = out["call_sid"]
    assert call_id.startswith("smstest_") and out["dry_run"] is True
    assert out["patient"]["patient_id"] == "TEST-0001" and out["consent"] == "simulator"
    # Sent at once, in quiet hours, into the thread.
    assert len(out["messages"]) == 1 and "Acme Health" in out["messages"][0]["body"]
    consents = [d for _, d in run(sim(api).store.query("sms_consents"))]
    assert [c["source"] for c in consents] == ["sms_simulator"]

    r = reply(client, "3")
    assert r.status_code == 200 and "date of birth" in outbound(r)[-1]
    r = reply(client, "03/15/1950")
    assert r.json()["state"] == st.STATE_SURVEY and len(outbound(r)) == 2
    assert survey.Q_SUGAR in sim_session(api)["survey_plan"]

    asked = [False]
    for _ in range(40):
        if sim_session(api)["state"] != st.STATE_SURVEY:
            break
        r = reply(client, answer_current(api, asked))
        assert r.status_code == 200, r.text
    out = r.json()
    assert out["state"] == st.STATE_CLOSED and out["report_id"]
    assert out["report"]["follow_up"] == "urgent_same_day" and out["report"]["follow_up_reason"]
    assert "care team" in outbound(r)[-1]

    # The report and the call live in the simulator's namespace, marked as a test.
    reps = run(sim(api).store.query("reports"))
    assert len(reps) == 1
    rep = reps[0][1]
    assert rep["channel"] == "sms" and rep["call_id"] == call_id
    qs = rep["survey_responses"]["patient_questions"]
    assert [q["category"] for q in qs] == ["medication"]
    call = run(sim(api).store.get("calls", call_id))
    assert call["channel"] == "sms" and call["follow_up"] == "urgent_same_day"
    assert call["placed_via"] == "sms_simulator" and call["test"] is True

    # Requirement 1: every text stopped at the dry-run provider.
    sent = [m for m in out["messages"] if m["direction"] == "outbound"]
    assert len(sent) == len(sim(api).sms.sent) > 5
    assert all(m["message_sid"].startswith("DRYRUN-") for m in sent)
    assert all(to == PHONE for to, _ in sim(api).sms.sent)
    # Requirement 4: no real call, report, session, message, consent, budget,
    # filing, outbox record, queue entry or dial; the console and the follow-up
    # lists do not see the test.
    assert_no_real_writes(api)
    assert client.get(f"/api/calls/{call_id}").status_code == 404
    assert client.get("/api/sms/sessions").json()["sessions"] == []
    assert client.get("/api/sms/consent", params={"phone": PHONE}).json()["granted"] is False
    # The survey safety net, translation and knowledge jobs were dropped, not queued.
    kinds = {t["payload"].get("kind") for t in sim(api).tasks.dropped}
    assert "submit_survey" in kinds


def test_start_is_audited():
    client, api = make()
    assert start(client).status_code == 200
    (entry,) = [d for _, d in run(api.store.query("audit_log"))]
    assert entry["action"] == "POST /api/sms/test/start" and entry["by"] == "admin@example.org"


# --------------------------------------------------------------------------- real vs simulator


def _real_missed_call(client, api, call_id=REAL_CALL):
    """A real patient who missed a call (fictional), as the call-terminal hook leaves it."""
    entry_id = new_id()
    run(
        api.store.set(
            "call_list",
            entry_id,
            {
                "phone": PHONE,
                "patient_name": "Avery Quinn",
                "patient_id": "P-REAL-01",
                "patient_dob": "1948-07-02",
                "list_status": "pending",
                "attempts": 1,
            },
        )
    )
    run(
        api.store.set(
            "calls",
            call_id,
            {
                "call_sid": call_id,
                "to": PHONE,
                "call_status": "rna",
                "entry_id": entry_id,
                "patient_id": "P-REAL-01",
                "patient_name": "Avery Quinn",
                "patient_context": {
                    "first_name": "Avery",
                    "patient_name": "Avery Quinn",
                    "patient_id": "P-REAL-01",
                },
                "placed_via": "call_list",
                "initiated_at": DAY.isoformat(),
            },
        )
    )
    r = client.post("/tasks/sms-missed-call", json={"call_id": call_id})
    assert r.status_code == 200 and r.json()["queued"] is True
    (task,) = [t for t in api.tasks.tasks if t["payload"].get("kind") == "send_invite"]
    api.tasks.tasks.remove(task)
    return client.post(task["path"], json=task["payload"]).json()


def _real_consent(client):
    r = client.post(
        "/api/sms/consent",
        json={"phone": PHONE, "granted": True, "statement_version": "v1"},
        headers=USER,
    )
    assert r.status_code == 200


def _real_text(client, body, n):
    return client.post(
        "/tasks/sms-inbound", json={"from": PHONE, "body": body, "message_id": f"SM{n:032x}"}
    )


def test_a_real_inbound_text_during_a_simulator_session_takes_the_real_path():
    client, api = make(provider=FakeSms(), at=DAY)
    _real_consent(client)
    assert _real_missed_call(client, api) == {"sent": True}
    assert start(client).status_code == 200
    assert reply(client, "3").json()["state"] == st.STATE_AWAITING_DOB
    sim_before = sim_session(api)
    real_invite = api.sms.bodies(PHONE)
    assert len(real_invite) == 1 and "Avery" in real_invite[0]

    # The real patient replies "1": their own conversation answers, through the
    # live provider, and a real recall is queued for THEIR call.
    r = _real_text(client, "1", 1)
    assert r.status_code == 200 and r.json()["effect"] == "dispatch_recall"
    real = real_session(api)
    assert real["call_sid"] == REAL_CALL and real["state"] == st.STATE_RECALL_DISPATCHED
    assert len(api.sms.sent) == 2 and "call you shortly" in api.sms.bodies()[-1]
    assert [t["payload"]["kind"] for t in api.tasks.tasks] == ["dispatch_recall"]
    # The simulator's conversation did not move and got no message.
    assert sim_session(api) == sim_before
    msgs = run(sim(api).store.query("sms_messages"))
    assert all(d["call_sid"] != REAL_CALL for _, d in msgs)
    real_msgs = [d for _, d in run(api.store.query("sms_messages"))]
    assert {d["call_sid"] for d in real_msgs} == {REAL_CALL}
    assert all(not str(d["message_sid"]).startswith("DRYRUN-") for d in real_msgs)


def test_a_real_inbound_text_with_only_a_simulator_session_is_not_answered_by_it():
    client, api = make()
    assert start(client).status_code == 200
    sim_before = sim_session(api)
    # "3" would move the simulator's conversation to the DOB gate.
    r = _real_text(client, "3", 2)
    assert r.status_code == 200
    assert sim_session(api) == sim_before
    assert api.sms.sent == []  # no real consent, no real session: nothing to answer with
    assert real_session(api) is None
    real_msgs = [d for _, d in run(api.store.query("sms_messages"))]
    assert len(real_msgs) == 1 and real_msgs[0]["call_sid"] is None


def test_a_real_missed_call_invite_during_a_simulator_session():
    client, api = make(provider=FakeSms(), at=DAY)
    assert start(client).status_code == 200
    sim_before = sim_session(api)
    # The simulator's consent is not a real one: the real invite is refused.
    r = _real_missed_call(client, api)
    assert r["blocked"].startswith("No SMS consent")
    assert api.sms.sent == []
    # With the patient's real consent it goes out, as if no simulator existed.
    _real_consent(client)
    assert _real_missed_call(client, api, call_id="CA" + "8" * 32) == {"sent": True}
    assert len(api.sms.sent) == 1 and "Avery" in api.sms.bodies(PHONE)[0]
    real = real_session(api)
    assert real["call_sid"] == "CA" + "8" * 32 and real["state"] == st.STATE_AWAITING_CHOICE
    assert sim_session(api) == sim_before
    # And the simulator keeps going on its own conversation.
    assert reply(client, "3").json()["state"] == st.STATE_AWAITING_DOB
    assert real_session(api)["state"] == st.STATE_AWAITING_CHOICE
    assert len(api.sms.sent) == 1


def test_reply_never_reaches_a_real_conversation():
    client, api = make()
    run(api.store.set("sms_sessions", PHONE, {"state": st.STATE_AWAITING_CHOICE}))
    r = reply(client, "3")
    assert r.status_code == 409 and "start one first" in r.json()["detail"]
    assert real_session(api) == {"state": st.STATE_AWAITING_CHOICE}
    assert sim(api).sms.sent == [] and api.sms.sent == []


def test_a_real_session_does_not_block_a_test_and_is_left_alone():
    client, api = make()
    real = {
        "state": st.STATE_SURVEY,
        "call_sid": REAL_CALL,
        "patient_context": {"patient_name": "Avery Quinn"},
    }
    run(api.store.set("sms_sessions", PHONE, real))
    assert start(client).status_code == 200
    assert reply(client, "3").status_code == 200
    assert real_session(api) == real


# --------------------------------------------------------------------------- calls and opt-outs


def test_call_me_now_never_rings():
    client, api = make()
    assert start(client).status_code == 200
    r = reply(client, "1")
    assert r.status_code == 200 and r.json()["state"] == st.STATE_RECALL_DISPATCHED
    (task,) = [t for t in sim(api).tasks.dropped if t["payload"].get("kind") == "dispatch_recall"]
    # Even if that task were delivered to the real handler, it finds nothing.
    res = client.post("/tasks/sms-action", json=task["payload"])
    assert "call_id" not in res.json()
    # And on the simulator's side the dialler refuses the test patient.
    assert "error" in run(sms.dispatch_recall(sim(api), task["payload"]))
    assert api.voice.placed == []
    assert_no_real_writes(api)


def test_a_booked_time_is_never_dialled():
    client, api = make()
    assert start(client).status_code == 200
    reply(client, "2")
    r = reply(client, "tomorrow 3pm")
    assert r.status_code == 200 and "You're set for" in outbound(r)[-1]
    (cb,) = run(sim(api).store.query("scheduled_callbacks"))
    assert cb[1]["status"] == "scheduled"
    (task,) = [t for t in sim(api).tasks.dropped if t["payload"].get("kind") == "dispatch_callback"]
    assert client.post("/tasks/sms-action", json=task["payload"]).json() == {
        "skipped": "no callback"
    }
    assert "error" in run(sms.dispatch_callback(sim(api), task["payload"]))
    assert api.voice.placed == []
    assert client.get("/api/sms/callbacks").json()["callbacks"] == []
    assert_no_real_writes(api)


@pytest.mark.parametrize(("word", "language"), [("STOP", None), ("ALTO", "es-US")])
def test_an_opt_out_in_the_simulator_never_opts_the_real_number_out(word, language):
    client, api = make()
    _real_consent(client)
    run(api.store.set("outbound_allowlist", PHONE, {"label": "Avery"}))
    kw = {"language": language} if language else {}
    assert start(client, **kw).json()["consent"] == "simulator"
    r = reply(client, word)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["effect"] == "opt_out"
    assert r.json()["state"] == st.STATE_OPTED_OUT
    assert run(sim(api).store.get("sms_optouts", PHONE))["opted_out"] is True
    # The real number: not opted out, consent intact, still on the allowlist.
    assert run(api.store.get("sms_optouts", PHONE)) is None
    assert client.get("/api/sms/consent", params={"phone": PHONE}).json()["granted"] is True
    assert run(api.store.get("outbound_allowlist", PHONE)) == {"label": "Avery"}
    assert api.sms.sent == []
    # A new test starts clean.
    r = start(client, **kw)
    assert r.status_code == 200 and r.json()["consent"] == "simulator"


def test_a_number_really_opted_out_is_refused():
    client, api = make()
    run(api.store.set("sms_optouts", PHONE, {"opted_out": True}))
    r = start(client)
    assert r.status_code == 409 and "opted out" in r.json()["detail"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_simulator_consent_only_counts_in_dry_run(dry_run):
    _, api = make(dry_run=dry_run, provider=FakeSms())
    run(
        sms.grant_consent(
            api, PHONE, statement_version="x", granted_by="t", source=sms.SIMULATOR_SOURCE
        )
    )
    assert (run(sms.active_consent(api, PHONE)) is not None) is dry_run


# --------------------------------------------------------------------------- languages (step 5)


def test_a_spanish_test_uses_the_draft_wording_and_says_so():
    from voa_core.translate import FakeTranslator

    client, api = make()
    api.translator = FakeTranslator({"bastante bien": ("es", "pretty good"), "sí": ("es", "yes")})
    r = start(client, language="es-US")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["wording"] == {
        "language": "es-US",
        "name": "Spanish (US)",
        "status": "draft",
        "approved": False,
        "label": "Draft wording, not approved",
    }
    invite_text = out["messages"][0]["body"]
    assert invite_text.startswith("Acme Health tiene un mensaje de seguimiento para Jordan.")
    assert invite_text.endswith("Responda STOP para cancelar.")
    s = sim_session(api)
    assert s["sms_language"] == "es-US" and s["sms_draft_wording"] is True
    assert s["patient_context"]["language"] == "es-US"

    assert "DD/MM/AAAA" in outbound(reply(client, "3"))[-1]
    r = reply(client, "15/03/1950")
    assert r.json()["state"] == st.STATE_SURVEY
    assert outbound(r)[-1].endswith("¿Cómo se siente en general desde su procedimiento?")
    # The reply takes the real path: translated to English, read, original kept.
    r = reply(client, "bastante bien")
    assert outbound(r)[-1].endswith("¿Tiene algún dolor ahora? Responda SÍ o NO.")
    answers = sim_session(api)["survey_answers"]
    assert answers["overall_feeling"] == "pretty good"
    assert answers["_original"]["overall_feeling"] == "bastante bien"
    assert api.translator.calls == [["bastante bien"]]
    assert isinstance(sim(api).sms, CountingDryRun)
    assert_no_real_writes(api)


def test_english_is_the_default_and_unchanged():
    client, api = make()
    out = start(client).json()
    assert out["wording"]["language"] == "en-US" and out["wording"]["label"] == ""
    assert "sms_language" not in sim_session(api)
    assert out["messages"][0]["body"].startswith("Acme Health has a follow-up message")


def test_a_language_without_text_wording_or_not_supported_is_refused():
    client, api = make()
    r = start(client, language="vi-VN")
    assert r.status_code == 409 and "Vietnamese" in r.json()["detail"]
    assert start(client, language="xx-XX").status_code == 400
    assert sim(api).sms.sent == [] and written(api) == set()


def test_the_draft_wording_never_leaves_through_a_real_provider():
    # A real session carrying the simulator's flag still cannot use a draft on
    # the real Api: only the simulator's own dry-run Api can.
    client, api = make(provider=FakeSms())
    s = {
        "sms_language": "es-US",
        "sms_draft_wording": True,
        "patient_context": {sms.SIMULATOR_FLAG: True, "language": "es-US"},
    }
    assert sms.wording_block(api, s) == "No approved Spanish (US) text wording."
    assert sms.wording_block(sim(api), s) is None
    assert start(client, language="es-US").status_code == 200
    assert api.sms.sent == [] and len(sim(api).sms.sent) == 1
