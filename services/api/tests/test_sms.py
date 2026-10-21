"""SMS follow-up (phase 5): missed call -> invite -> reply -> callback / survey.

Fictional data only. Every external edge is a fake: MemoryStore, MemoryTaskQueue
(tasks are drained by POSTing them, as Cloud Tasks would), FakeVoice, FakeSms,
and a stub (or an offline fake model) for the Gemini time fallback.
"""

import asyncio
import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from voa_agents.sms import AdkTimeInterpreter, SmsConversation
from voa_api import sms_service as sms
from voa_api.app import create_app
from voa_api.context import Api
from voa_core import copy as messages
from voa_core.config import load_config
from voa_core.copy import SiteCopy
from voa_core.sms import states as st
from voa_core.sms.conversation import ConversationPolicy
from voa_core.store import MemoryStore, new_id
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice
from voa_providers.sms import DryRunSms, FakeSms, classify_twilio_error

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # Tuesday 10:00 in New York
NIGHT = datetime(2026, 3, 4, 3, 30, tzinfo=UTC)  # Tuesday 22:30 in New York
PHONE = "+12025550101"
OTHER = "+12025550199"
DOB = "1950-03-15"
CALL = "CA" + "1" * 32
CALL2 = "CA" + "2" * 32

_ids = itertools.count(1)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def make(env="test", allowlist=PHONE, survey=True, **sms_cfg):
    environ = {
        "VOA_ENV": env,
        "VOA__GCP__PROJECT_ID": "test-project",
        "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
        "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
        "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
        "VOA__DIALING__ALLOWLIST": allowlist,
        "VOA__SITE__ORG_NAME": "Acme Health",
        "VOA__SMS__SURVEY_ENABLED": str(survey).lower(),
        **{f"VOA__SMS__{k.upper()}": str(v) for k, v in sms_cfg.items()},
    }
    cfg = load_config(environ=environ)
    clock = Clock(NOW)
    api = Api(cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=clock)
    api.sms = FakeSms()
    api.sms_interpreter = None
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api, clock


def run(coro):
    return asyncio.run(coro)


def drain(client, api, path="/tasks/sms-action", kind=None):
    out = []
    for t in [t for t in api.tasks.tasks if t["path"] == path]:
        if kind and t["payload"].get("kind") != kind:
            continue
        api.tasks.tasks.remove(t)
        out.append(client.post(t["path"], json=t["payload"]))
    return out


def queued(api, kind):
    return [t for t in api.tasks.tasks if t["payload"].get("kind") == kind]


def text(client, body, phone=PHONE, mid=None):
    mid = mid or f"SM{next(_ids):032x}"
    return client.post("/tasks/sms-inbound", json={"from": phone, "body": body, "message_id": mid})


def seed_call(api, call_id=CALL, status="rna", phone=PHONE, with_entry=True, diabetic=False):
    entry_id = None
    if with_entry:
        entry_id = new_id()
        run(
            api.store.set(
                "call_list",
                entry_id,
                {
                    "phone": phone,
                    "patient_name": "Jordan Rivera",
                    "patient_id": "P-001",
                    "patient_dob": DOB,
                    "is_diabetic": diabetic,
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
                "to": phone,
                "call_status": status,
                "entry_id": entry_id,
                "patient_id": "P-001",
                "patient_name": "Jordan Rivera",
                "patient_context": {
                    "first_name": "Jordan",
                    "patient_name": "Jordan Rivera",
                    "patient_id": "P-001",
                    "is_diabetic": diabetic,
                },
                "placed_via": "call_list",
                "initiated_at": NOW.isoformat(),
            },
        )
    )
    return entry_id


def consent(client, phone=PHONE, granted=True):
    r = client.post(
        "/api/sms/consent",
        json={"phone": phone, "granted": granted, "statement_version": "v1"},
        headers={"X-Acting-User": "nurse@example.org"},
    )
    assert r.status_code == 200
    return r


def invite(client, api, call_id=CALL):
    r = client.post("/tasks/sms-missed-call", json={"call_id": call_id})
    assert r.status_code == 200
    return drain(client, api, kind="send_invite")


def session(api, phone=PHONE):
    return run(api.store.get("sms_sessions", phone))


def ready(**kw):
    """A patient who missed a call, has consented, and has been sent the invite."""
    client, api, clock = make(**kw)
    entry_id = seed_call(api)
    consent(client)
    res = invite(client, api)
    assert res[0].json() == {"sent": True}
    return client, api, clock, entry_id


# --------------------------------------------------------------------------- invites


def test_missed_call_invites_once_per_cooldown_and_the_switch_reopens_it():
    client, api, clock = make()
    seed_call(api)
    consent(client)
    r = client.post("/tasks/sms-missed-call", json={"call_id": CALL})
    assert r.json()["queued"] is True and r.json()["delay_s"] == 60
    dup = client.post("/tasks/sms-missed-call", json={"call_id": CALL})
    assert dup.json()["queued"] is False  # named task: a duplicate collapses
    assert drain(client, api, kind="send_invite")[0].json() == {"sent": True}
    assert run(api.store.get("calls", CALL))["sms_follow_up"]["status"] == "sent"
    body = api.sms.bodies(PHONE)[0]
    assert "Acme Health" in body and "Jordan" in body and "Rivera" not in body
    assert "Reply STOP" in body and "3 = answer by text" in body
    assert session(api)["state"] == st.STATE_AWAITING_CHOICE

    # A second unanswered call the same day: no second text.
    seed_call(api, call_id=CALL2)
    assert invite(client, api, CALL2)[0].json() == {"skipped": "invite cooldown"}
    assert len(api.sms.sent) == 1
    stamp = run(api.store.get("calls", CALL2))["sms_follow_up"]
    assert stamp["status"] == "not_sent" and "last 24 hours" in stamp["reason"]

    # The operator switch (for demos) lets it through, loudly.
    r = client.post("/api/sms/settings", json={"name": "invite_cooldown_enabled", "value": False})
    assert r.json()["settings"] == {"invite_cooldown_enabled": False}
    assert (
        client.post("/api/sms/settings", json={"name": "bogus", "value": True}).status_code == 400
    )
    seed_call(api, call_id="CA" + "3" * 32)
    assert invite(client, api, "CA" + "3" * 32)[0].json() == {"sent": True}


def test_no_invite_without_consent_and_no_invite_for_an_answered_call():
    client, api, _ = make()
    seed_call(api)
    assert invite(client, api)[0].json()["blocked"].startswith("No SMS consent")
    assert api.sms.sent == []
    s = session(api)
    assert s["state"] == st.STATE_CLOSED and "consent" in s["last_error"]
    assert s["last_invite_at"] is None  # rolled back: nothing was said
    assert s["expires_at"] == sms.NEVER  # terminal: out of the expiry scan (C-13)
    # The call says why no text went out, so a nurse can see it on the voicemail.
    stamp = run(api.store.get("calls", CALL))["sms_follow_up"]
    assert stamp["status"] == "not_sent" and stamp["reason"].startswith("No SMS consent")

    seed_call(api, call_id=CALL2, status="success")
    r = client.post("/tasks/sms-missed-call", json={"call_id": CALL2})
    assert r.json()["skipped"] == "call is success"


def test_quiet_hours_defer_the_invite_at_queue_time_and_at_send_time():
    client, api, clock = make()
    seed_call(api)
    consent(client)
    clock.at = NIGHT
    r = client.post("/tasks/sms-missed-call", json={"call_id": CALL}).json()
    # 22:30 -> 08:00 next morning, not a minute after the missed call.
    assert r["delay_s"] == pytest.approx(9.5 * 3600)

    # Review S-4: a task that fires at night (paused queue resumed, retry...) is
    # re-checked when it runs and deferred again rather than sent.
    assert drain(client, api, kind="send_invite")[0].json()["deferred"].startswith("2026-03-04T13")
    assert api.sms.sent == [] and session(api) is None
    clock.at = NIGHT + timedelta(hours=10)  # 08:30 local
    assert drain(client, api, kind="send_invite")[0].json() == {"sent": True}


def test_provider_failures_opted_out_permanent_and_retryable():
    # 21610: the carrier says STOP. Recorded as an opt-out; never retried.
    client, api, _ = make()
    seed_call(api)
    consent(client)
    api.sms.fail_code = 21610
    r = invite(client, api)[0]
    assert r.status_code == 200 and "carrier" in r.json()["blocked"]
    assert run(api.store.get("sms_optouts", PHONE))["opted_out"] is True
    assert client.get("/api/sms/consent", params={"phone": PHONE}).json()["granted"] is False
    assert run(api.store.get("rate_limits", "sms_sends"))["count"] == 0  # budget released

    # A server error is retryable: 503 so Cloud Tasks tries again, and the
    # cooldown stamp is rolled back so the retry is not blocked by it.
    client, api, _ = make()
    seed_call(api)
    consent(client)
    api.sms.fail_status, api.sms.fail_times = 500, 1
    client.post("/tasks/sms-missed-call", json={"call_id": CALL})
    task = queued(api, "send_invite")[0]
    assert client.post(task["path"], json=task["payload"]).status_code == 503
    assert session(api)["last_invite_at"] is None
    assert client.post(task["path"], json=task["payload"]).json() == {"sent": True}

    # An invalid number (21211, a 4xx): permanent, 2xx, not retried.
    client, api, _ = make()
    seed_call(api)
    consent(client)
    api.sms.fail_code, api.sms.fail_status = 21211, 400
    r = invite(client, api)[0]
    assert r.status_code == 200 and "21211" in r.json()["blocked"]


def test_twilio_error_classification_and_dry_run_never_logs_the_body(caplog):
    assert classify_twilio_error(400, 21610).opted_out
    assert classify_twilio_error(400, 21614).permanent
    assert classify_twilio_error(None, 21408).permanent
    assert not classify_twilio_error(429, 20429).permanent
    assert not classify_twilio_error(503, 20500).permanent
    assert not classify_twilio_error(None, "9" * 5000).permanent  # S-10: no int() blow-up
    sid = run(DryRunSms().send(PHONE, "fever yes, pain 9"))
    assert sid.startswith("DRYRUN-")
    rec = [r for r in caplog.records if getattr(r, "event", "") == "sms.dry_run"][-1]
    assert rec.to == "+1***0101" and rec.chars == len("fever yes, pain 9")
    assert not any("fever" in str(v) for v in vars(rec).values())


def test_a_missing_key_permission_reads_clearly_on_the_call():
    from voa_providers.sms import SmsSendError

    err = classify_twilio_error(403, 8021)
    assert err.permanent and "Messaging permissions" in str(err)
    assert isinstance(err, SmsSendError)

    class Refusing:
        name = "twilio"
        sent: list = []

        async def send(self, to, body):
            raise classify_twilio_error(403, 8021)

    client, api, _ = make()
    api.sms = Refusing()
    run(sms.grant_consent(api, PHONE, statement_version="v1", granted_by="test", source="test"))
    with pytest.raises(sms.SendBlocked) as e:
        run(sms.send_sms(api, PHONE, "hello"))
    assert e.value.reason.startswith("Twilio refused the message: the Twilio API key")
    assert "(code 8021)" in e.value.reason


# --------------------------------------------------------------------------- replies


def test_reply_1_calls_now_through_the_dialer_exactly_once():
    client, api, _, entry_id = ready()
    r = text(client, "1")
    assert r.json()["effect"] == "dispatch_recall"
    assert "we'll call you shortly" in api.sms.bodies()[-1]
    s = session(api)
    assert s["state"] == st.STATE_RECALL_DISPATCHED and s["recall_id"]
    task = queued(api, "dispatch_recall")[0]
    first = client.post(task["path"], json=task["payload"]).json()
    again = client.post(task["path"], json=task["payload"]).json()  # duplicate delivery
    assert "call_id" in first and again == {"skipped": "recall no longer wanted"}
    assert len(api.voice.placed) == 1
    call = run(api.store.get("calls", first["call_id"]))
    assert call["placed_via"] == "sms_recall" and call["entry_id"] == entry_id
    assert DOB not in json.dumps(call)  # C-2 still holds for SMS-placed calls
    assert session(api)["state"] == st.STATE_CLOSED


def test_recall_with_no_dob_on_file_is_declined_not_promised():
    client, api, _ = make()
    seed_call(api, with_entry=False)  # a dashboard call: no DOB anywhere
    consent(client)
    invite(client, api)
    r = text(client, "1")
    assert r.json()["note"] == "undialable"
    assert "not able to place that call" in api.sms.bodies()[-1]
    assert queued(api, "dispatch_recall") == [] and api.voice.placed == []


def test_reply_2_books_a_time_and_the_callback_dials_through_the_dialer():
    client, api, clock, _ = ready()
    text(client, "2")
    assert session(api)["state"] == st.STATE_AWAITING_TIME
    # Review C-7: a bare "2" arriving again (redelivered under a new id, or typed
    # twice) is NOT two o'clock.
    r = text(client, "2")
    assert r.json()["note"] == "reask" and queued(api, "dispatch_callback") == []
    assert run(api.store.query("scheduled_callbacks")) == []

    text(client, "tomorrow 3pm")
    assert "You're set for Wed Mar 4, 3:00 PM" in api.sms.bodies()[-1]
    cbs = client.get("/api/sms/callbacks").json()["callbacks"]
    assert len(cbs) == 1 and cbs[0]["status"] == "scheduled"
    assert cbs[0]["run_at"].startswith("2026-03-04T20:00")
    assert "patient_context" not in cbs[0] and DOB not in json.dumps(cbs)
    task = queued(api, "dispatch_callback")[0]
    assert task["delay_s"] == pytest.approx(29 * 3600)
    s = session(api)
    assert s["state"] == st.STATE_SCHEDULED and s["scheduled_for_text"] == "Wed Mar 4, 3:00 PM"

    clock.at = NOW + timedelta(hours=29)
    res = drain(client, api, kind="dispatch_callback")[0].json()
    assert run(api.store.get("calls", res["call_id"]))["placed_via"] == "sms_callback"
    cb = client.get("/api/sms/callbacks").json()["callbacks"][0]
    assert cb["status"] == "dispatched" and cb["call_sid"] == res["call_id"]
    assert session(api)["state"] == st.STATE_CLOSED
    again = client.post(task["path"], json=task["payload"]).json()
    assert again == {"skipped": "callback is dispatched"} and len(api.voice.placed) == 1


def test_rebooking_supersedes_and_dashboard_cancel_stops_the_dial():
    client, api, _, _ = ready()
    text(client, "2")
    text(client, "tomorrow 3pm")
    text(client, "thursday 10am")  # a new time replaces the old booking
    cbs = {c["status"] for c in client.get("/api/sms/callbacks").json()["callbacks"]}
    assert cbs == {"superseded", "scheduled"}
    live = next(
        c
        for c in client.get("/api/sms/callbacks").json()["callbacks"]
        if c["status"] == "scheduled"
    )
    r = client.delete(f"/api/sms/callbacks/{live['callback_id']}")
    assert r.json() == {"status": "canceled", "callback_id": live["callback_id"]}
    assert client.delete(f"/api/sms/callbacks/{live['callback_id']}").status_code == 409
    assert client.delete("/api/sms/callbacks/" + "f" * 32).status_code == 404
    s = session(api)
    assert s["state"] == st.STATE_CLOSED and s["scheduled_for"] is None
    for res in drain(client, api, kind="dispatch_callback"):
        assert res.json()["skipped"].startswith("callback is")
    assert api.voice.placed == []


def test_unclear_menu_reply_reasks_once_then_closes():
    client, api, _, _ = ready()
    text(client, "what is this")
    assert "Sorry, I didn't catch that" in api.sms.bodies()[-1]
    text(client, "hmm")
    assert session(api)["state"] == st.STATE_CLOSED
    n = len(api.sms.sent)
    text(client, "hello?")  # a closed conversation gets the closed note, not the menu
    assert "This follow-up is closed" in api.sms.bodies()[-1] and len(api.sms.sent) == n + 1


def test_duplicate_inbound_message_id_is_processed_once():
    client, api, _, _ = ready()
    mid = "SM" + "a" * 32
    assert text(client, "2", mid=mid).json()["note"] == "ask-time"
    # The redelivery of the SAME "2" must not be read as 2:00 PM in awaiting_time.
    assert text(client, "2", mid=mid).json() == {"duplicate": True}
    assert session(api)["state"] == st.STATE_AWAITING_TIME
    assert run(api.store.query("scheduled_callbacks")) == []
    inbound = [
        m
        for m in client.get("/api/sms/messages", params={"phone": PHONE}).json()["messages"]
        if m["direction"] == "inbound"
    ]
    assert len(inbound) == 1


def test_inbound_failure_before_persisting_is_retried_not_lost():
    client, api, _, _ = ready()
    real = api.tasks.enqueue

    async def broken(*a, **k):
        raise RuntimeError("queue down")

    api.tasks.enqueue = broken
    mid = "SM" + "b" * 32
    assert text(client, "1", mid=mid).status_code == 503
    assert session(api)["state"] == st.STATE_AWAITING_CHOICE  # rolled back
    assert all("shortly" not in b for b in api.sms.bodies())  # nothing promised
    api.tasks.enqueue = real
    assert text(client, "1", mid=mid).json()["effect"] == "dispatch_recall"  # the retry runs


def test_unknown_sender_is_ignored():
    client, api, _ = make()
    assert text(client, "1", phone=OTHER).json()["note"] == "no-session"
    assert text(client, "hi", phone="12345").json() == {"ignored": "sender is not E.164"}
    assert api.sms.sent == []


# --------------------------------------------------------------------------- STOP / START / HELP


def test_stop_opts_out_cancels_and_removes_from_the_dial_allowlist_start_restores_consent():
    client, api, _ = make(allowlist=OTHER)  # PHONE is only on the runtime list
    assert client.post("/api/allowlist", json={"phone": PHONE}).status_code == 201
    seed_call(api)
    consent(client)
    invite(client, api)
    text(client, "2")
    text(client, "tomorrow 3pm")
    sent_before = len(api.sms.sent)

    r = text(client, "STOP")
    assert r.json()["effect"] == "opt_out"
    assert len(api.sms.sent) == sent_before  # the carrier answers STOP itself
    assert session(api)["state"] == st.STATE_OPTED_OUT
    assert run(api.store.get("outbound_allowlist", PHONE)) is None  # voice suppressed
    cb = client.get("/api/sms/callbacks").json()["callbacks"][0]
    assert cb["status"] == "canceled"
    c = client.get("/api/sms/consent", params={"phone": PHONE}).json()
    assert c["granted"] is False and len(c["history"]) == 1
    assert c["history"][0]["withdrawn_at"] and c["history"][0]["withdrawn_by"] == "sms:STOP"
    # Nothing is sent and nothing is dialled after STOP.
    assert drain(client, api, kind="dispatch_callback")[0].json()["skipped"].startswith("callback")
    assert text(client, "1").json()["note"] == "opted-out"
    with pytest.raises(sms.SendBlocked, match="opted out"):
        run(sms.send_sms(api, PHONE, "hello"))

    text(client, "START")
    c = client.get("/api/sms/consent", params={"phone": PHONE}).json()
    assert c["granted"] is True and len(c["history"]) == 2  # appended, history kept
    assert c["consent"]["granted_by"] == "sms:START"
    assert c["consent"]["statement_version"].startswith("start-keyword/")
    assert run(api.store.get("sms_optouts", PHONE))["opted_out"] is False
    assert run(api.store.get("outbound_allowlist", PHONE)) is None  # NOT restored


def test_start_from_a_stranger_creates_no_consent():
    client, api, _ = make()
    text(client, "START", phone=OTHER)
    assert client.get("/api/sms/consent", params={"phone": OTHER}).json()["history"] == []


def test_help_and_stop_reply_only_when_we_answer_keywords_ourselves():
    client, api, _, _ = ready()
    n = len(api.sms.sent)
    text(client, "HELP")
    assert len(api.sms.sent) == n

    client, api, _, _ = ready(reply_to_compliance_keywords=True)
    text(client, "HELP")
    assert "Reply STOP to opt out" in api.sms.bodies()[-1]
    text(client, "STOP")
    assert "unsubscribed" in api.sms.bodies()[-1]  # the one permitted confirmation
    with pytest.raises(sms.SendBlocked):
        run(sms.send_sms(api, PHONE, "anything else"))


def test_consent_history_is_kept_on_withdrawal():
    client, api, _ = make()
    consent(client)
    consent(client, granted=False)
    c = client.get("/api/sms/consent", params={"phone": PHONE}).json()
    assert c["granted"] is False and c["consent"] is None
    assert len(c["history"]) == 1 and c["history"][0]["withdrawn_at"]
    assert c["history"][0]["granted_by"] == "nurse@example.org"
    consent(client)
    c = client.get("/api/sms/consent", params={"phone": PHONE}).json()
    assert c["granted"] is True and len(c["history"]) == 2
    assert client.post("/api/sms/consent", json={"phone": "555"}).status_code == 422
    assert client.get("/api/sms/consent", params={"phone": "555"}).status_code == 400


# --------------------------------------------------------------------------- the survey


# Non-diabetic plan (7): overall, pain, fever, incision, legs swollen, sleeping
# well, questions for the care team.
CLEAN_SURVEY = ("pretty good", "no", "no", "no", "no", "yes", "no")
URGENT_SURVEY = ("ok", "yes", "9", "my knee", "worse", "yes", "no", "no", "yes", "no")


def answer_survey(client, answers):
    text(client, "3")
    text(client, "03/15/1950")
    for a in answers:
        text(client, a)


def test_survey_by_text_files_a_report_onto_the_call():
    client, api, _, entry_id = ready()
    text(client, "3")
    assert (
        "date of birth for Jordan"
        in client.get("/api/sms/messages", params={"phone": PHONE}).json()["messages"][-1]["body"]
    )
    text(client, "03/15/1950")
    bodies = api.sms.bodies()
    assert "that's verified" in bodies[-2] and bodies[-1].startswith("(1/7)")
    for a in CLEAN_SURVEY:
        text(client, a)
    closing = api.sms.bodies()[-1]
    assert "Your care team has your answers" in closing
    assert messages.MEDICATION_LIST_REMINDER in closing
    assert messages.QUESTIONS_FOLLOW_UP not in closing

    reps = run(api.store.query("reports"))
    assert len(reps) == 1
    rep = reps[0][1]
    assert rep["channel"] == "sms" and rep["outcome"] == "questionnaire_completed"
    assert rep["call_id"] == CALL and rep["dob_verification_result"] == "verified"
    assert rep["follow_up"] == "none"
    call = run(api.store.get("calls", CALL))
    assert call["call_status"] == "success" and call["channel"] == "sms"
    assert call["report_doc_id"] == reps[0][0]
    assert run(api.store.get("call_list", entry_id))["list_status"] == "completed"
    assert session(api)["state"] == st.STATE_CLOSED

    # The typed DOB never reaches the dashboard (S-8), nor does any session view.
    thread = client.get("/api/sms/messages", params={"phone": PHONE, "call_sid": CALL}).json()
    assert "03/15/1950" not in json.dumps(thread) and sms.DOB_REDACTED in json.dumps(thread)
    views = client.get("/api/sms/sessions").json()
    assert DOB not in json.dumps(views) and "patient_context" not in json.dumps(views)

    # The safety-net copy of the filing finds it done: no second report, no second closing.
    n = len(api.sms.sent)
    assert drain(client, api, kind="submit_survey")[0].json() == {
        "skipped": "already filed and closed"
    }
    assert len(run(api.store.query("reports"))) == 1 and len(api.sms.sent) == n


def test_urgent_survey_answers_get_the_urgent_closing_and_tier():
    client, api, _, _ = ready()
    answer_survey(client, URGENT_SURVEY)
    assert "someone from your care team will reach out" in api.sms.bodies()[-1]
    assert messages.MEDICATION_LIST_REMINDER in api.sms.bodies()[-1]
    rep = run(api.store.query("reports"))[0][1]
    assert rep["follow_up"] == "urgent_same_day"
    assert rep["survey_responses"]["pain_severity"] == 9
    assert "pain_severity" in rep["survey_responses"]["_triggers"]


def test_red_flag_mid_survey_files_an_emergency_and_sends_911_copy():
    client, api, _, _ = ready()
    answer_survey(client, ["not great"])
    r = text(client, "I have chest pain")
    assert r.json()["effect"] == "file_emergency"
    assert "call 911 now" in api.sms.bodies()[-1]
    rep = run(api.store.query("reports"))[0][1]
    assert rep["follow_up"] == "emergency" and rep["outcome"] == "emergency_ended"
    assert rep["channel"] == "sms" and rep["survey_responses"]["overall_feeling"] == "not great"
    assert run(api.store.get("calls", CALL))["follow_up"] == "emergency"
    assert session(api)["state"] == st.STATE_CLOSED


def test_a_late_survey_reopens_a_signed_off_call_and_withdraws_its_export():
    """B-5: the by-text questionnaire files onto the original call. A nurse signed
    that call off first; an urgent report must bring it back, not hide behind the
    sign-off, and the record waiting for the EHR is withdrawn."""
    client, api, _, _ = ready()
    run(
        api.store.merge(
            "calls",
            CALL,
            {
                "outcome": "questionnaire_completed",
                "follow_up": "none",
                "review": {"status": "signed_off", "by": "nurse@example.org"},
            },
        )
    )
    run(api.store.set("ehr_outbox", CALL, {"status": "pending_export"}))
    answer_survey(client, URGENT_SURVEY)
    call = run(api.store.get("calls", CALL))
    assert call["follow_up"] == "urgent_same_day"
    assert call["review"]["status"] == "reopened" and call["review"]["reopened_by"] == "report"
    assert run(api.store.get("ehr_outbox", CALL))["status"] == "withdrawn"


def test_a_late_survey_does_not_uncancel_a_patient():
    """Review of B-2: a nurse's cancel stands against a late report."""
    client, api, _, entry_id = ready()
    run(api.store.merge("call_list", entry_id, {"list_status": "canceled"}))
    answer_survey(client, CLEAN_SURVEY)
    entry = run(api.store.get("call_list", entry_id))
    assert entry["list_status"] == "canceled" and entry["last_call_status"] == "success"


def test_closing_send_failure_is_retried_without_refiling():
    # Review S-3: a retryable failure on the closing is retried by the queued
    # filing task, which sends the closing and does NOT file a second report.
    client, api, _, _ = ready()
    answer_survey(client, CLEAN_SURVEY[:-1])
    api.sms.fail_status, api.sms.fail_times = 503, 1
    text(client, "no")
    assert len(run(api.store.query("reports"))) == 1
    assert session(api)["state"] == st.STATE_SURVEY  # not closed: the closing never went
    assert drain(client, api, kind="submit_survey")[0].json() == {"filed": True, "closed": True}
    assert "That's everything" in api.sms.bodies()[-1]
    assert messages.MEDICATION_LIST_REMINDER in api.sms.bodies()[-1]
    assert len(run(api.store.query("reports"))) == 1


def test_a_medication_question_by_text_files_urgent_same_day():
    client, api, _, _ = ready()
    answer_survey(client, [*CLEAN_SURVEY[:-1], "yes"])
    assert api.sms.bodies()[-1] == messages.FOLLOWUP_TEXT["patient_questions"]
    text(client, "How often should I take my pain pills")
    assert api.sms.bodies()[-1].endswith(messages.ANOTHER_QUESTION)
    text(client, "When is my follow-up appointment")
    text(client, "no")
    rep = run(api.store.query("reports"))[0][1]
    assert rep["outcome"] == "questionnaire_completed"
    assert rep["follow_up"] == "urgent_same_day"
    qs = rep["survey_responses"]["patient_questions"]
    assert [q["category"] for q in qs] == ["medication", "appointment"]
    assert [q["priority"] for q in qs] == ["urgent_same_day", "routine_next_business_day"]
    assert "patient_questions" in rep["survey_responses"]["_triggers"]
    closing = api.sms.bodies()[-1]
    assert messages.QUESTIONS_FOLLOW_UP in closing
    assert messages.MEDICATION_LIST_REMINDER in closing
    assert "someone from your care team will reach out" in closing


def test_a_question_typed_straight_in_reply_files_with_the_closing():
    client, api, _, _ = ready()
    answer_survey(client, [*CLEAN_SURVEY[:-1], "can I drive yet", "no"])
    rep = run(api.store.query("reports"))[0][1]
    assert rep["survey_responses"]["has_questions"] == "yes"
    assert rep["survey_responses"]["patient_questions"][0]["text"] == "can I drive yet"
    assert rep["follow_up"] == "routine_next_business_day"
    assert messages.QUESTIONS_FOLLOW_UP in api.sms.bodies()[-1]


def test_c_stops_the_survey_and_files_only_when_something_urgent_is_known():
    client, api, _, _ = ready()
    answer_survey(client, ["fine", "no", "yes"])  # fever: yes
    text(client, "C")
    rep = run(api.store.query("reports"))[0][1]
    assert rep["follow_up"] == "urgent_same_day" and "outcome" not in rep
    assert run(api.store.get("calls", CALL))["call_status"] == "success"

    client, api, _, _ = ready()
    answer_survey(client, ["fine"])
    text(client, "C")
    assert run(api.store.query("reports")) == []


def test_two_wrong_birthdates_file_wrong_person_without_settling_the_call():
    client, api, _, entry_id = ready()
    text(client, "3")
    text(client, "01/01/1980")
    text(client, "02/02/1981")
    rep = run(api.store.query("reports"))[0][1]
    assert rep["outcome"] == "unable_to_reach_wrong_person"
    assert rep["dob_verification_result"] == "mismatch"
    assert run(api.store.get("calls", CALL))["call_status"] == "rna"  # not promoted
    assert run(api.store.get("call_list", entry_id))["list_status"] == "pending"


# --------------------------------------------------------------------------- expiry


def test_expiry_scan_never_goes_blind_and_files_an_abandoned_urgent_survey():
    # Review C-13: 201 finished conversations must not hide the one live session.
    client, api, clock, _ = ready()
    for i in range(201):
        run(
            api.store.set(
                "sms_sessions",
                f"closed-{i}",
                {"state": st.STATE_CLOSED, "expires_at": sms.NEVER, "updated_at": "x"},
            )
        )
    answer_survey(client, ["ok", "no", "yes"])  # fever: yes, then silence
    clock.at = NOW + timedelta(hours=49)
    r = client.post("/cron/sms-expire").json()
    assert r["expired"] == 1 and r["filed"] == 1 and r["scanned"] == 1
    assert session(api)["state"] == st.STATE_EXPIRED
    assert session(api)["expires_at"] == sms.NEVER
    rep = run(api.store.query("reports"))[0][1]
    assert rep["follow_up"] == "urgent_same_day" and "outcome" not in rep
    assert client.post("/cron/sms-expire").json()["scanned"] == 0


# --------------------------------------------------------------------------- auth


def test_routes_are_restricted_to_their_callers():
    client, api, _ = make(env="prod")
    assert client.get("/api/sms/sessions").status_code == 403
    assert client.post("/tasks/sms-inbound", json={}).status_code == 403
    assert client.post("/cron/sms-expire").status_code == 403


# --------------------------------------------------------------------------- the Gemini fallback


class StubInterpreter:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def interpret_time(self, text, *, now, tz):
        self.calls.append(text)
        return self.answer


def test_llm_candidate_is_validated_and_needs_a_yes_before_booking():
    client, api, _, _ = ready()
    stub = StubInterpreter("2026-03-05T13:00")
    api.sms_interpreter = stub
    text(client, "2")
    text(client, "2")  # C-7: a bare digit never reaches the model
    assert stub.calls == []
    r = text(client, "once the kids are asleep")
    assert r.json()["note"] == "llm-candidate"
    assert "did you mean Thu Mar 5, 1:00 PM" in api.sms.bodies()[-1]
    assert run(api.store.query("scheduled_callbacks")) == []  # nothing booked yet
    text(client, "yes")
    assert "You're set for Thu Mar 5, 1:00 PM" in api.sms.bodies()[-1]
    cb = client.get("/api/sms/callbacks").json()["callbacks"]
    assert len(cb) == 1 and cb[0]["run_at"].startswith("2026-03-05T18:00")
    assert session(api).get("pending_time") is None


def test_llm_candidate_outside_the_window_is_clamped_and_no_means_no():
    client, api, _, _ = ready()
    api.sms_interpreter = StubInterpreter("2026-03-04T23:30")
    text(client, "2")
    text(client, "once the kids are in bed")
    assert "closest we can do is Thu Mar 5, 8:00 AM" in api.sms.bodies()[-1]
    text(client, "no")
    assert api.sms.bodies()[-1].startswith("What day and time works?")
    assert run(api.store.query("scheduled_callbacks")) == []


def test_llm_garbage_or_failure_falls_back_to_the_deterministic_reask():
    client, api, _, _ = ready()
    api.sms_interpreter = StubInterpreter("not a date")
    text(client, "2")
    assert text(client, "whenever really").json()["note"] == "reask"


class _FakeModel:
    """Offline stand-in for Gemini behind a real ADK LlmAgent + Runner."""

    @staticmethod
    def build(reply: str):
        from google.adk.models import BaseLlm, LlmResponse
        from google.genai import types

        class FakeLlm(BaseLlm):
            async def generate_content_async(self, llm_request, stream=False):
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=reply)])
                )

        return FakeLlm(model="fake-model")


def test_adk_interpreter_runs_through_the_runner_offline():
    from google.adk.agents import LlmAgent
    from voa_agents.sms.agent import _INSTRUCTION, TimeCandidate

    def agent(reply):
        return LlmAgent(
            name="sms_time_interpreter",
            model=_FakeModel.build(reply),
            instruction=_INSTRUCTION,
            output_schema=TimeCandidate,
            output_key="candidate",
            include_contents="none",
        )

    ok = AdkTimeInterpreter(
        agent=agent('{"understood": true, "local_datetime": "2026-03-05T13:00"}')
    )
    assert run(ok.interpret_time("after lunch thu", now=NOW, tz="America/New_York")) == (
        "2026-03-05T13:00"
    )
    unsure = AdkTimeInterpreter(agent=agent('{"understood": false}'))
    assert run(unsure.interpret_time("whenever", now=NOW, tz="America/New_York")) is None


def test_conversation_without_interpreter_is_the_deterministic_machine():
    conv = SmsConversation(
        site=SiteCopy(org_name="Acme Health", support_email="support@example.org"),
        policy=ConversationPolicy(),
        scheduling=sms.SchedulingPolicy(),
    )
    s = {"state": st.STATE_AWAITING_TIME, "retry_count": 0}
    d = run(conv.decide(s, "once the kids are asleep", NOW))
    assert d.note == "reask" and d.effect.value == "none"
