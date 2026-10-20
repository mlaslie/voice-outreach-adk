"""The company name set in the admin console drives what patients hear and read."""

import pytest
from test_sms import CALL, PHONE, consent, drain, make, seed_call
from voa_core import organization


@pytest.fixture(autouse=True)
def _fresh_cache():
    organization._cache.clear()
    yield
    organization._cache.clear()


def test_validate():
    assert organization.validate("  Riverside   Health ") == "Riverside Health"
    for bad in ("", "x", "{org_name}", "A" * 61, "Café Health"):
        with pytest.raises(ValueError):
            organization.validate(bad)


def test_set_reset_and_report_the_company_name():
    client, api, _ = make()
    body = client.get("/api/call-settings").json()["organization"]
    assert body == {"org_name": "Acme Health", "default": "Acme Health", "is_default": True}
    r = client.put("/api/call-settings/organization", json={"org_name": "Riverside Health"})
    assert r.status_code == 200 and r.json()["organization"]["org_name"] == "Riverside Health"
    assert (
        client.put("/api/call-settings/organization", json={"org_name": "{x}"}).status_code == 422
    )
    r = client.put("/api/call-settings/organization", json={"org_name": ""})
    assert r.json()["organization"]["is_default"] is True


def test_the_missed_call_text_uses_the_admin_name():
    client, api, _ = make()
    client.put("/api/call-settings/organization", json={"org_name": "Riverside Health"})
    seed_call(api)
    consent(client)
    client.post("/tasks/sms-missed-call", json={"call_id": CALL})
    assert drain(client, api, kind="send_invite")[0].json() == {"sent": True}
    body = api.sms.bodies(PHONE)[0]
    assert "Riverside Health" in body and "Acme Health" not in body


def test_the_voice_instruction_uses_the_name_in_session_state():
    from types import SimpleNamespace

    from voa_agents.voice.agent import build_voice_agent
    from voa_core.config import load_config

    cfg = load_config(environ={"VOA_ENV": "test", "VOA__SITE__ORG_NAME": "Acme Health"})
    agent = build_voice_agent(cfg)
    ctx = SimpleNamespace(state={"patient": {"first_name": "J"}, "org_name": "Riverside Health"})
    text = agent.instruction(ctx)
    assert "Riverside Health" in text and "Acme Health" not in text
