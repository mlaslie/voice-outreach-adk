"""Sharing one SMS number with another system (docs/sms-number-switch.md)."""

import pytest
from test_sms import PHONE, make, run
from voa_api import sms_control, sms_service
from voa_providers.sms import FakeNumberControl

OURS = "https://gw.example.org/twilio/sms"
PEER = "https://peer.example.org/api/sms-inbound"
NURSE = {"X-Acting-User": "rn@example.org"}


def shared(sms_url=PEER, **kw):
    client, api, clock = make(
        switch_enabled="true", peer_webhook_url=PEER, voice_number="+12025550100", **kw
    )
    api.number_control = FakeNumberControl(sms_url)
    return client, api


def test_url_matching_ignores_case_slash_and_query():
    client, api = shared()
    assert sms_control.owner_of(api, "HTTPS://GW.example.org/twilio/sms/?x=1") == "self"
    assert sms_control.owner_of(api, PEER + "/") == "peer"
    assert sms_control.owner_of(api, "https://elsewhere.example.org/sms") == "unknown"
    assert sms_control.owner_of(api, "") == "unknown"


def test_either_hostname_of_a_system_counts_but_only_the_primary_is_written():
    client, api, _ = make(
        switch_enabled="true",
        peer_webhook_url=PEER,
        peer_webhook_aliases="https://peer-abc-uc.example.org/api/sms-inbound",
        self_webhook_aliases="https://gw-abc-uc.example.org/twilio/sms/",
        voice_number="+12025550100",
    )
    api.number_control = FakeNumberControl("https://PEER-abc-uc.example.org/api/sms-inbound")
    assert sms_control.owner_of(api, "https://gw-abc-uc.example.org/twilio/sms") == "self"
    assert client.get("/api/sms/control", headers=NURSE).json()["active"] == "peer"
    client.post("/api/sms/control", json={"action": "take"}, headers=NURSE)
    assert api.number_control.sms_url == OURS


def test_status_names_the_active_system():
    client, api = shared(PEER)
    s = client.get("/api/sms/control", headers=NURSE).json()
    assert (s["active"], s["active_name"], s["number_last4"]) == ("peer", "GE-CX", "0100")
    api.number_control.sms_url = "https://elsewhere.example.org/sms"
    s = client.get("/api/sms/control?fresh=true", headers=NURSE).json()
    assert s["active"] == "unknown" and s["webhook_host"] == "elsewhere.example.org"


def test_take_and_give_only_touch_the_sms_url_and_are_audited():
    client, api = shared(PEER)
    r = client.post("/api/sms/control", json={"action": "take"}, headers=NURSE)
    assert r.status_code == 200 and r.json()["active"] == "self"
    assert api.number_control.sms_url == OURS
    r = client.post("/api/sms/control", json={"action": "give"}, headers=NURSE)
    assert r.json()["active"] == "peer" and api.number_control.updates == [OURS, PEER]
    log = [d for _, d in run(api.store.query("sms_control_log"))]
    assert {(d["action"], d["from"], d["to"], d["by"]) for d in log} == {
        ("take", "GE-CX", "ADK", "rn@example.org"),
        ("give", "ADK", "GE-CX", "rn@example.org"),
    }
    assert client.post("/api/sms/control", json={"action": "steal"}).status_code == 422


def test_give_needs_the_peer_url_and_the_switch_must_be_on():
    client, api, _ = make(switch_enabled="true", voice_number="+12025550100")
    api.number_control = FakeNumberControl(OURS)
    assert client.post("/api/sms/control", json={"action": "give"}).status_code == 409
    client, api, _ = make(voice_number="+12025550100")
    api.number_control = FakeNumberControl(PEER)
    assert client.post("/api/sms/control", json={"action": "take"}).status_code == 409
    assert api.number_control.updates == []


async def _send(api):
    await sms_service.grant_consent(
        api, PHONE, statement_version="v1", granted_by="test", source="test"
    )
    return await sms_service.send_sms(api, PHONE, "hello")


def test_no_real_text_while_the_other_system_has_the_number():
    client, api = shared(PEER)
    with pytest.raises(sms_service.SendBlocked, match="GE-CX has control"):
        run(_send(api))
    assert api.sms.sent == []
    client.post("/api/sms/control", json={"action": "take"}, headers=NURSE)
    run(_send(api))
    assert len(api.sms.sent) == 1


def test_an_unreadable_number_fails_closed():
    client, api = shared(OURS)
    api.number_control.fail = True
    with pytest.raises(sms_service.SendBlocked):
        run(_send(api))
    assert client.get("/api/sms/control", headers=NURSE).json()["active"] == "unknown"


def test_dry_run_is_not_gated_and_no_switch_means_no_check():
    from voa_providers.sms import DryRunSms

    client, api = shared(PEER)
    api.sms = DryRunSms()
    run(_send(api))  # the other system has the number, yet a dry-run send is not refused
    client, api, _ = make()
    run(_send(api))
    assert len(api.sms.sent) == 1


def test_a_missing_key_permission_reads_clearly_on_the_call():
    from voa_providers.sms import SmsSendError, classify_twilio_error

    err = classify_twilio_error(403, 8021)
    assert err.permanent and "Messaging permissions" in str(err)

    class Refusing:
        name = "twilio"
        sent: list = []

        async def send(self, to, body):
            raise classify_twilio_error(403, 8021)

    client, api = shared("https://gw.example.org/twilio/sms")
    api.sms = Refusing()
    with pytest.raises(sms_service.SendBlocked) as e:
        run(_send(api))
    assert e.value.reason.startswith("Twilio refused the message: the Twilio API key")
    assert "(code 8021)" in e.value.reason
    assert isinstance(err, SmsSendError)
