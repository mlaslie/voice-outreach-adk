"""Dry-run SMS simulator (/api/sms/test/*). Fictional data only; nothing is sent.

Every outbound message must come from ``DryRunSms``: the provider is wrapped so
any send through anything else fails the test.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from voa_api import sms_service as sms
from voa_api.app import create_app
from voa_api.context import Api
from voa_core import survey
from voa_core.config import load_config
from voa_core.sms import states as st
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice
from voa_providers.sms import DryRunSms, FakeSms

NOW = datetime(2026, 3, 4, 3, 30, tzinfo=UTC)  # 22:30 in New York: quiet hours
PHONE = "+12025550101"
STRANGER = "+12025550177"
USER = {"X-Acting-User": "nurse@example.org"}


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


def make(dry_run=True, survey_enabled=True, provider=None):
    environ = {
        "VOA_ENV": "test",
        "VOA__GCP__PROJECT_ID": "test-project",
        "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
        "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
        "VOA__SERVICES__UI_SA": "ui@example.iam.gserviceaccount.com",
        "VOA__DIALING__ALLOWLIST": PHONE,
        "VOA__SITE__ORG_NAME": "Acme Health",
        "VOA__SMS__SURVEY_ENABLED": str(survey_enabled).lower(),
        "VOA__SMS__DRY_RUN": str(dry_run).lower(),
    }
    cfg = load_config(environ=environ)
    api = Api(
        cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice(), clock=Clock(NOW)
    )
    api.sms = provider if provider is not None else CountingDryRun()
    api.sms_interpreter = None
    client = TestClient(create_app(cfg, api))
    client.__enter__()
    return client, api


def run(coro):
    return asyncio.run(coro)


def start(client, phone=PHONE, **kw):
    return client.post("/api/sms/test/start", json={"phone": phone, **kw}, headers=USER)


def reply(client, body, phone=PHONE):
    return client.post("/api/sms/test/reply", json={"phone": phone, "body": body}, headers=USER)


def outbound(res):
    return [m["body"] for m in res.json()["new_messages"]]


def test_refused_unless_dry_run():
    client, api = make(dry_run=False, provider=FakeSms())
    r = start(client)
    assert r.status_code == 409 and "dry-run" in r.json()["detail"]
    assert reply(client, "3").status_code == 409
    assert api.sms.sent == [] and run(api.store.query("sms_sessions")) == []
    assert run(api.store.query("calls")) == []


def test_refused_when_the_provider_is_not_the_dry_run_one():
    client, api = make(provider=FakeSms())
    assert start(client).status_code == 409
    assert api.sms.sent == []


def test_refused_for_a_number_off_the_allowlist_or_not_e164():
    client, api = make()
    r = start(client, phone=STRANGER)
    assert r.status_code == 403
    assert start(client, phone="555-0101").status_code == 400
    assert reply(client, "3", phone=STRANGER).status_code == 403
    assert api.sms.sent == [] and run(api.store.query("sms_consents")) == []


def test_reply_never_reaches_a_real_conversation():
    client, api = make()
    run(api.store.set("sms_sessions", PHONE, {"state": st.STATE_AWAITING_CHOICE}))
    r = reply(client, "3")
    assert r.status_code == 409 and "start one first" in r.json()["detail"]
    assert api.sms.sent == []


def answer_current(api, asked_question: list[bool]) -> str:
    """Answer whatever the session is on: clean except one medication question."""
    s = run(api.store.get("sms_sessions", PHONE))
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


def test_full_text_run_files_an_urgent_report_and_sends_nothing():
    client, api = make()
    r = start(client, diabetic=True)
    assert r.status_code == 200, r.text
    out = r.json()
    call_id = out["call_sid"]
    assert call_id.startswith("smstest_") and out["dry_run"] is True
    assert out["patient"]["patient_id"] == "TEST-0001" and out["consent"] == "simulator"
    # Sent at once, in quiet hours, into the thread.
    assert len(out["messages"]) == 1 and "Acme Health" in out["messages"][0]["body"]
    consents = [d for _, d in run(api.store.query("sms_consents"))]
    assert [c["source"] for c in consents] == ["sms_simulator"]

    r = reply(client, "3")
    assert r.status_code == 200 and "date of birth" in outbound(r)[-1]
    r = reply(client, "03/15/1950")
    assert r.json()["state"] == st.STATE_SURVEY and len(outbound(r)) == 2
    assert survey.Q_SUGAR in run(api.store.get("sms_sessions", PHONE))["survey_plan"]

    asked = [False]
    for _ in range(40):
        if run(api.store.get("sms_sessions", PHONE))["state"] != st.STATE_SURVEY:
            break
        r = reply(client, answer_current(api, asked))
        assert r.status_code == 200, r.text
    assert r.json()["state"] == st.STATE_CLOSED and r.json()["report_id"]
    assert "care team" in outbound(r)[-1]

    reps = run(api.store.query("reports"))
    assert len(reps) == 1
    rep = reps[0][1]
    assert rep["channel"] == "sms" and rep["call_id"] == call_id
    assert rep["follow_up"] == "urgent_same_day"
    qs = rep["survey_responses"]["patient_questions"]
    assert [q["category"] for q in qs] == ["medication"]
    call = run(api.store.get("calls", call_id))
    assert call["channel"] == "sms" and call["follow_up"] == "urgent_same_day"
    assert call["placed_via"] == "sms_simulator" and call["survey_responses"]
    # The console can open it.
    assert client.get(f"/api/calls/{call_id}").status_code == 200

    # Every outbound went through DryRunSms, and only through it.
    assert isinstance(api.sms, CountingDryRun)
    thread = client.get("/api/sms/messages", params={"phone": PHONE, "call_sid": call_id})
    sent = [m for m in thread.json()["messages"] if m["direction"] == "outbound"]
    assert len(sent) == len(api.sms.sent) > 5
    assert all(m["message_sid"].startswith("DRYRUN-") for m in sent)
    assert all(to == PHONE for to, _ in api.sms.sent)


def test_call_me_now_never_rings_and_stop_is_refused():
    client, api = make()
    assert start(client).status_code == 200
    r = reply(client, "STOP")
    assert r.status_code == 400
    assert run(api.store.get("sms_optouts", PHONE)) is None

    r = reply(client, "1")
    assert r.status_code == 200 and r.json()["state"] == st.STATE_RECALL_DISPATCHED
    for t in [t for t in api.tasks.tasks if t["payload"].get("kind") == "dispatch_recall"]:
        res = client.post(t["path"], json=t["payload"])
        assert "error" in res.json()
    assert api.voice.placed == []


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
    s = run(api.store.get("sms_sessions", PHONE))
    assert s["sms_language"] == "es-US" and s["sms_draft_wording"] is True
    assert s["patient_context"]["language"] == "es-US"

    assert "DD/MM/AAAA" in outbound(reply(client, "3"))[-1]
    r = reply(client, "15/03/1950")
    assert r.json()["state"] == st.STATE_SURVEY
    assert outbound(r)[-1].endswith("¿Cómo se siente en general desde su procedimiento?")
    # The reply takes the real path: translated to English, read, original kept.
    r = reply(client, "bastante bien")
    assert outbound(r)[-1].endswith("¿Tiene algún dolor ahora? Responda SÍ o NO.")
    answers = run(api.store.get("sms_sessions", PHONE))["survey_answers"]
    assert answers["overall_feeling"] == "pretty good"
    assert answers["_original"]["overall_feeling"] == "bastante bien"
    assert api.translator.calls == [["bastante bien"]]
    # Spanish STOP words are refused like STOP (they would opt the number out).
    assert reply(client, "ALTO").status_code == 400
    assert isinstance(api.sms, CountingDryRun)


def test_english_is_the_default_and_unchanged():
    client, api = make()
    out = start(client).json()
    assert out["wording"]["language"] == "en-US" and out["wording"]["label"] == ""
    assert "sms_language" not in run(api.store.get("sms_sessions", PHONE))
    assert out["messages"][0]["body"].startswith("Acme Health has a follow-up message")


def test_a_language_without_text_wording_or_not_supported_is_refused():
    client, api = make()
    r = start(client, language="vi-VN")
    assert r.status_code == 409 and "Vietnamese" in r.json()["detail"]
    assert start(client, language="xx-XX").status_code == 400
    assert api.sms.sent == [] and run(api.store.query("sms_sessions")) == []


def test_the_draft_wording_never_leaves_through_a_real_provider():
    # The same staged session, but the live provider is not the dry-run one: the
    # simulator refuses up front, and send_sms refuses the draft wording too.
    client, api = make(provider=FakeSms())
    assert start(client, language="es-US").status_code == 409
    s = {
        "sms_language": "es-US",
        "sms_draft_wording": True,
        "patient_context": {sms.SIMULATOR_FLAG: True, "language": "es-US"},
    }
    assert sms.wording_block(api, s) == "No approved Spanish (US) text wording."
    assert api.sms.sent == []
