"""Who is the signed-in Gemini Enterprise user?

Gemini Enterprise sends two credentials (a2a-cloud-run-gemini-enterprise skill):

* ``x-serverless-authorization``: the Discovery Engine service agent's JWT, which
  Cloud Run IAM consumes. It names the SAME account for every user, so it is never
  read as a person;
* ``authorization``: an opaque ``ya29.`` token for the signed-in user, present
  when an authorization resource is configured on the Gemini Enterprise agent.

The opaque token is introspected at ``oauth2.googleapis.com/tokeninfo`` and its
audience checked against our OAuth client id before the email is trusted. Results
are cached by a SHA-256 of the token (never the token itself), bounded, and a
failed lookup is cached only briefly (review A-4). The token, the URL carrying
it and exception text are never logged.

Identity is NOT the session key: sessions stay keyed to the conversation
(contextId), so a token that resolves on one turn and not the next cannot strand
a conversation.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping

import httpx

log = logging.getLogger(__name__)

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
CACHE_MAX = 256
HIT_TTL_S = 300
MISS_TTL_S = 30


class IdentityResolver:
    def __init__(self, oauth_client_id: str, *, client: httpx.AsyncClient | None = None):
        self.client_id = oauth_client_id
        self._client = client
        self._cache: OrderedDict[str, tuple[float, str | None]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)

    async def resolve(self, headers: Mapping[str, str]) -> str | None:
        if not self.client_id:
            # Say why nobody can be identified; otherwise the allowlist gate refuses
            # every user with no trace of the cause in the logs.
            has_bearer = any(k.lower() == "authorization" for k in headers)
            log.warning(
                "identity not resolved: VOA_NURSE_OAUTH_CLIENT_ID is not set",
                extra={"event": "nurse.identity.not_configured", "bearer_present": has_bearer},
            )
            return None
        lower = {k.lower(): v for k, v in headers.items()}
        auth = lower.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            log.warning(
                "identity not resolved: no bearer token",
                extra={"event": "nurse.identity.missing"},
            )
            return None
        token = auth[7:].strip()
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        email = await self._introspect(token)
        self._cache[key] = (now + (HIT_TTL_S if email else MISS_TTL_S), email)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        return email

    async def _introspect(self, token: str) -> str | None:
        try:
            if self._client is not None:
                r = await self._client.get(TOKENINFO_URL, params={"access_token": token})
            else:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    r = await c.get(TOKENINFO_URL, params={"access_token": token})
            claims = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            log.warning(
                "identity not resolved: tokeninfo unreachable",
                extra={"event": "nurse.identity.error"},
            )
            return None
        aud_ok = self.client_id in (claims.get("aud"), claims.get("azp"))
        email = claims.get("email")
        if aud_ok and email and str(claims.get("email_verified", "true")).lower() == "true":
            return str(email).lower()
        log.warning(
            "identity not resolved",
            extra={
                "event": "nurse.identity.rejected",
                "status": r.status_code,
                "audience_match": aud_ok,
                "email_present": bool(email),
            },
        )
        return None
