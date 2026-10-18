"""End-user identity from the opaque bearer Gemini Enterprise passes through."""

import httpx
from voa_nurse.identity import CACHE_MAX, IdentityResolver

CLIENT_ID = "123-acme.apps.googleusercontent.com"


def _resolver(claims: dict, status: int = 200, counter: list | None = None) -> IdentityResolver:
    def handler(req):
        if counter is not None:
            counter.append(1)
        return httpx.Response(status, json=claims)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return IdentityResolver(CLIENT_ID, client=client)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_resolves_the_email_when_the_audience_matches():
    r = _resolver({"aud": CLIENT_ID, "email": "RN.Lee@example.org", "email_verified": "true"})
    assert await r.resolve(_h("ya29.a")) == "rn.lee@example.org"


async def test_rejects_a_token_for_another_client():
    r = _resolver({"aud": "someone-else", "email": "rn.lee@example.org"})
    assert await r.resolve(_h("ya29.a")) is None


async def test_disabled_without_a_client_id():
    assert await IdentityResolver("").resolve(_h("ya29.a")) is None


async def test_no_bearer_no_identity():
    r = _resolver({"aud": CLIENT_ID, "email": "rn.lee@example.org"})
    assert await r.resolve({"x-serverless-authorization": "Bearer service.agent.jwt"}) is None


async def test_cache_is_hashed_bounded_and_reused():
    """Review A-4: keyed by a hash (never the raw token), bounded, one lookup per token."""
    calls: list = []
    r = _resolver({"aud": CLIENT_ID, "email": "rn.lee@example.org"}, counter=calls)
    for i in range(CACHE_MAX + 20):
        assert await r.resolve(_h(f"token-{i}")) == "rn.lee@example.org"
    await r.resolve(_h(f"token-{CACHE_MAX + 19}"))
    assert len(r._cache) == CACHE_MAX and len(calls) == CACHE_MAX + 20
    assert not any("token-" in k for k in r._cache)


async def test_a_failed_lookup_is_cached_only_briefly():
    r = _resolver({}, status=400)
    assert await r.resolve(_h("ya29.bad")) is None
    ((exp, email),) = r._cache.values()
    import time

    assert email is None and exp - time.monotonic() <= 30
