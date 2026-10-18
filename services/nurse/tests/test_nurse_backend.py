"""The outreach API client: ID-token auth for the API's audience, X-Acting-User,
timeouts, error mapping and the bounded list reads (httpx mocked)."""

import base64
import json
import time

import httpx
import pytest
from nurse_fixtures import make_turn
from voa_nurse import turn
from voa_nurse.backend import UNVERIFIED_USER, Backend, BackendError, IdTokenSource

API = "https://voa-api.example.org"


def _jwt(exp: float) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'RS256'})}.{b64({'exp': exp, 'aud': API})}.sig"


class Minter:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, audience: str) -> str:
        self.calls.append(audience)
        return _jwt(time.time() + 3600)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)


async def test_sends_an_id_token_for_the_api_audience_and_the_acting_user():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json={"calls": [{"doc_id": "CA1"}], "count": 1})

    minter = Minter()
    b = Backend(API, client=_client(handler), tokens=IdTokenSource(API, fetch=minter))
    with turn.active(make_turn(backend=b, user="rn.lee@example.org")):
        calls, truncated = await b.list_calls()
        await b.list_calls()
    assert calls == [{"doc_id": "CA1"}] and truncated is False
    assert minter.calls == [API]  # minted once, then cached
    req = seen[0]
    assert (
        req.headers["authorization"].startswith("Bearer ")
        and req.headers["authorization"].count(".") == 2
    )
    assert req.headers["x-acting-user"] == "rn.lee@example.org"
    assert req.url.path == "/api/calls" and req.url.params["limit"] == "500"


async def test_unverified_user_is_marked_not_impersonated():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"entries": []})

    b = Backend(API, client=_client(handler), tokens=IdTokenSource(API, fetch=Minter()))
    with turn.active(make_turn(backend=b, user=None)):
        await b.get_call_list()
    assert seen[0].headers["x-acting-user"] == UNVERIFIED_USER
    assert seen[0].url.params["limit"] == "1000"


async def test_no_token_for_a_local_api():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"runs": []})

    minter = Minter()
    b = Backend(
        "http://localhost:8081",
        client=_client(handler),
        tokens=IdTokenSource("http://localhost:8081", fetch=minter),
    )
    await b.get_call_runs()
    assert "authorization" not in seen[0].headers and minter.calls == []


async def test_token_is_refreshed_near_expiry():
    tokens = iter([_jwt(time.time() + 60), _jwt(time.time() + 3600)])
    src = IdTokenSource(API, fetch=lambda aud: next(tokens))
    first = await src.token()
    second = await src.token()  # the first expires within the refresh margin
    assert first != second
    assert await src.token() == second


async def test_token_failure_is_a_backend_error_without_details():
    def boom(aud):
        raise RuntimeError("metadata server says no: secret-ish detail")

    with pytest.raises(BackendError) as e:
        await IdTokenSource(API, fetch=boom).token()
    assert "secret-ish" not in str(e.value)


async def test_timeouts_and_errors_become_backend_errors():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    b = Backend(API, client=_client(slow), tokens=IdTokenSource(API, fetch=Minter()))
    with pytest.raises(BackendError, match="did not answer in time"):
        await b.get_call("CA" + "1" * 32)

    def bad(req):
        return httpx.Response(422, json={"detail": [{"msg": "phone must be E.164"}]})

    b = Backend(API, client=_client(bad), tokens=IdTokenSource(API, fetch=Minter()))
    with pytest.raises(BackendError, match="422: phone must be E.164"):
        await b.place_call({})


def test_every_request_has_a_timeout():
    b = Backend(API, timeout_s=7)
    assert b._client.timeout.read == 7 and b._client.timeout.connect == 5


async def test_paths_match_the_api():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, dict(req.url.params)))
        body = {"settings": {}, "sessions": [], "callbacks": [], "entries": [], "runs": []}
        return httpx.Response(200, json=body)

    b = Backend(API, client=_client(handler), tokens=IdTokenSource(API, fetch=Minter()))
    cid, eid = "CA" + "1" * 32, "e" * 32
    await b.get_transcript(cid)
    await b.set_follow_up(cid, "none", "Spoke with patient.")
    await b.cancel_entries([eid], "Readmitted.")
    await b.reschedule_entry(eid, scheduled_for="2026-10-20T10:00:00-04:00")
    await b.start_run([eid])
    await b.remove_allowlist("+12025550101")
    await b.get_sessions()
    await b.cancel_callback("cb-1")
    await b.get_messages("+12025550101", cid)
    await b.set_sms_setting("invite_cooldown_enabled", False)
    paths = [(m, p) for m, p, _ in seen]
    assert paths == [
        ("GET", f"/api/calls/{cid}/transcript"),
        ("POST", f"/api/calls/{cid}/follow-up"),
        ("POST", "/api/call-list/cancel"),
        ("POST", f"/api/call-list/{eid}/reschedule"),
        ("POST", "/api/call-list/run"),
        ("DELETE", "/api/allowlist/+12025550101"),
        ("GET", "/api/sms/sessions"),
        ("DELETE", "/api/sms/callbacks/cb-1"),
        ("GET", "/api/sms/messages"),
        ("POST", "/api/sms/settings"),
    ]
    assert seen[6][2] == {"limit": "500"}
    assert seen[8][2] == {"phone": "+12025550101", "call_sid": cid}


async def test_a_full_page_is_reported_as_possibly_truncated():
    def handler(req):
        n = int(req.url.params["limit"])
        return httpx.Response(200, json={"calls": [{"doc_id": f"CA{i}"} for i in range(n)]})

    b = Backend(API, client=_client(handler), tokens=IdTokenSource(API, fetch=Minter()))
    calls, truncated = await b.list_calls()
    assert len(calls) == 500 and truncated is True


async def test_status_filter_is_sent_to_the_api():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"calls": []})

    b = Backend(API, client=_client(handler), tokens=IdTokenSource(API, fetch=Minter()))
    await b.list_calls(status="rna")
    assert seen == [{"limit": "500", "status": "rna"}]
