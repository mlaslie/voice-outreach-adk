"""The HTTP surface: agent card, /health, and A2A JSON-RPC over the real Starlette
app (httpx ASGI transport; no network)."""

import json
import os
import uuid

import httpx
from nurse_fixtures import FakeBackend, ScriptedLlm, make_cfg, make_settings
from voa_core.store import MemoryStore
from voa_nurse.app import build_deps, create_app
from voa_nurse.executor import EXT_V09


def _app(**settings):
    s = make_settings(**settings)
    deps = build_deps(
        make_cfg(), s, store=MemoryStore(), backend=FakeBackend(), model=ScriptedLlm()
    )
    return create_app(make_cfg(), s, deps=deps)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _rpc(method: str, text: str, context_id: str = "conv-1") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "message": {
                "role": "user",
                "messageId": uuid.uuid4().hex,
                "contextId": context_id,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


async def test_health():
    async with _client(_app()) as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_agent_card_points_at_the_public_url_and_declares_a2ui():
    async with _client(_app(public_url="https://nurse.example.org")) as c:
        card = (await c.get("/.well-known/agent-card.json")).json()
    assert card["url"] == "https://nurse.example.org/"
    assert card["protocolVersion"] == "0.3.0"
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False
    uris = [e["uri"] for e in card["capabilities"]["extensions"]]
    assert uris == [EXT_V09, "https://a2ui.org/a2a-extension/a2ui/v0.8"]
    v09 = card["capabilities"]["extensions"][0]["params"]["supportedCatalogIds"]
    assert "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json" in v09
    assert "localhost" not in json.dumps(card)


def test_settings_come_from_voa_nurse_env(monkeypatch):
    monkeypatch.setenv("VOA_NURSE_PUBLIC_URL", "https://nurse.example.org")
    monkeypatch.setenv("VOA_NURSE_ALLOWED_EMAILS", "RN.Lee@example.org, charge.rn@example.org")
    from voa_nurse.settings import NurseSettings

    s = NurseSettings()
    assert s.public_url == "https://nurse.example.org"
    assert s.allowed() == {"rn.lee@example.org", "charge.rn@example.org"}
    assert "VOA__NURSE__PUBLIC_URL" not in os.environ  # never the AppConfig namespace


async def test_message_send_returns_a2ui_when_requested():
    async with _client(_app()) as c:
        r = await c.post(
            "/", json=_rpc("message/send", "hello"), headers={"X-A2A-Extensions": EXT_V09}
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["status"]["state"] == "completed"
    parts = result["artifacts"][0]["parts"]
    assert parts and all(p["kind"] == "data" for p in parts)
    assert {p["metadata"]["mimeType"] for p in parts} == {"application/json+a2ui"}
    assert EXT_V09 in r.headers.get("x-a2a-extensions", "")


async def test_message_send_without_the_extension_is_text():
    async with _client(_app()) as c:
        r = await c.post("/", json=_rpc("message/send", "hello"))
    parts = r.json()["result"]["artifacts"][0]["parts"]
    assert [p["kind"] for p in parts] == ["text"]


async def test_message_stream_is_server_sent_events():
    """Gemini Enterprise uses message/stream; a JSON reply here is the error users see
    as "Expected response header Content-Type to contain 'text/event-stream'"."""
    async with _client(_app()) as c:
        r = await c.post(
            "/", json=_rpc("message/stream", "hello"), headers={"X-A2A-Extensions": EXT_V09}
        )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    kinds = [e["result"]["kind"] for e in events]
    assert kinds[-2:] == ["artifact-update", "status-update"]
    assert events[-1]["result"]["final"] is True


async def test_large_bodies_are_accepted_up_to_32mb():
    from voa_nurse.app import MAX_BODY

    assert MAX_BODY == 32 * 1024 * 1024
