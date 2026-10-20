"""Configuration changes leave an audit line; reads and failures do not."""

from test_api import NOW, make, run


def entries(client):
    return client.get("/api/audit").json()["entries"]


def test_a_successful_configuration_change_is_audited():
    client, api = make()
    client.put(
        "/api/call-settings/display",
        json={"time_format": "12h"},
        headers={"X-Acting-User": "admin@example.org"},
    )
    (e,) = entries(client)
    assert e["action"] == "PUT /api/call-settings/display"
    assert e["by"] == "admin@example.org" and e["at"]


def test_reads_and_failures_are_not_audited():
    client, api = make()
    client.get("/api/call-settings")
    client.put("/api/call-settings/display", json={"time_format": "military"})  # 422
    assert entries(client) == []


def test_phone_numbers_are_masked_and_bodies_are_not_kept():
    client, api = make()
    client.post("/api/allowlist", json={"phone": "+12025550199", "label": "Jordan's mobile"})
    client.delete("/api/allowlist/%2B12025550199")
    actions = {e["action"]: e for e in entries(client)}
    delete = actions["DELETE /api/allowlist/{phone}"]
    assert "+12025550199" not in str(delete) and delete["target"]["phone"].endswith("0199")
    assert "Jordan" not in str(actions["POST /api/allowlist"])


def test_call_events_summarise_the_timeline():
    client, api = make()
    cid = "CA" + "7" * 32
    run(
        api.store.set(
            "call_events",
            cid,
            {
                "timeline": [
                    {"ms": 0, "event": "media.start"},
                    {"ms": 950, "event": "agent.audio_start"},
                    {"ms": 4000, "event": "tool.call", "name": "verify_identity"},
                    {
                        "ms": 4100,
                        "event": "tool.response",
                        "name": "verify_identity",
                        "status": "match",
                    },
                    {"ms": 9000, "event": "agent.interrupted"},
                ]
            },
        )
    )
    s = client.get(f"/api/calls/{cid}/events").json()
    assert s["available"] and s["summary"] == {
        "events": 5,
        "first_audio_ms": 950,
        "tool_calls": 1,
        "interruptions": 1,
        "errors": 0,
    }
    assert NOW
