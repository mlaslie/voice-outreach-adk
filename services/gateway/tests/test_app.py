import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_gateway.app import Gateway, create_app, verify_softphone_token

TOKEN = "test-auth-token"  # noqa: S105
HOST = "gw.example.org"
SID = "CA" + "a" * 32


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOA_SECRET_TWILIO_AUTH_TOKEN", TOKEN)
    cfg = load_config(
        environ={
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": f"https://{HOST}",
            "VOA__SERVICES__STORE": "memory",
            "VOA__AMD__VM_DROP": "false",
            "VOA__PROVIDERS__TWILIO__AUTH_TOKEN": "secret://twilio-auth-token",
        }
    )
    store, tasks = MemoryStore(), MemoryTaskQueue()
    app = create_app(cfg, Gateway(cfg, store=store, tasks=tasks))
    with TestClient(app, base_url=f"https://{HOST}") as client:
        yield client, store, tasks


def signed_post(client, path, form):
    sig = RequestValidator(TOKEN).compute_signature(f"https://{HOST}{path}", form)
    return client.post(
        path,
        content=urlencode(form),
        headers={"X-Twilio-Signature": sig, "Content-Type": "application/x-www-form-urlencoded"},
    )


def test_unsigned_requests_are_refused(env):
    client, _, _ = env
    assert client.post("/twilio/answer", data={"CallSid": SID}).status_code == 403
    assert client.post("/twilio/status", data={"CallSid": SID}).status_code == 403


def test_answer_returns_stream_twiml(env):
    client, _, _ = env
    r = signed_post(client, "/twilio/answer", {"CallSid": SID})
    assert r.status_code == 200 and f"wss://{HOST}/twilio/media" in r.text


async def _seed(store, doc):
    await store.set("calls", SID, doc)


def test_status_flow_and_terminal_task(env):
    client, store, tasks = env
    import asyncio

    asyncio.run(_seed(store, {"call_sid": SID, "patient_context": {"patient_dob": "1950-03-15"}}))
    for status in ("ringing", "in-progress"):
        assert (
            signed_post(
                client, "/twilio/status", {"CallSid": SID, "CallStatus": status}
            ).status_code
            == 204
        )
    r = signed_post(
        client, "/twilio/status", {"CallSid": SID, "CallStatus": "completed", "CallDuration": "42"}
    )
    assert r.status_code == 204
    doc = asyncio.run(store.get("calls", SID))
    assert doc["call_status"] == "interrupted" and doc["duration_seconds"] == 42
    assert "patient_dob" not in doc.get("patient_context", {})  # C-2
    assert [t["path"] for t in tasks.tasks] == ["/tasks/call-terminal"]


def test_amd_only_callback_and_unknown_call_ignored(env):
    client, store, tasks = env
    import asyncio

    asyncio.run(_seed(store, {"call_sid": SID, "answered_at": "x", "call_status": "interrupted"}))
    assert (
        signed_post(
            client, "/twilio/status", {"CallSid": SID, "AnsweredBy": "machine_end_beep"}
        ).status_code
        == 204
    )
    doc = asyncio.run(store.get("calls", SID))
    assert doc["answered_by"] == "machine_end_beep" and doc["call_status"] == "left_vmail"
    other = "CA" + "b" * 32
    signed_post(client, "/twilio/status", {"CallSid": other, "CallStatus": "completed"})
    assert asyncio.run(store.get("calls", other)) is None  # no ghost docs (P-9)


def test_sms_inbound_is_queued_once_per_message_sid(env):
    client, _, tasks = env
    form = {"From": "+12025550101", "Body": "2", "MessageSid": "SM" + "c" * 32}
    for _ in range(2):  # Twilio redelivery
        assert signed_post(client, "/twilio/sms", form).status_code == 200
    assert len(tasks.tasks) == 1 and tasks.tasks[0]["path"] == "/tasks/sms-inbound"


def test_softphone_token():
    secret, call_id = "s3cret", "browser_" + "d" * 16
    exp = str(int(time.time()) + 60)
    sig = hmac.new(secret.encode(), f"{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    assert verify_softphone_token(secret, f"{call_id}.{exp}.{sig}") == call_id
    assert verify_softphone_token(secret, f"{call_id}.{exp}.{'0' * 64}") is None
    assert verify_softphone_token(secret, f"{call_id}.{exp}.{sig}", now=time.time() + 120) is None
