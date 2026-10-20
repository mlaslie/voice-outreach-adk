"""Client for the private outreach API (``services/api``).

* Authenticates with a Google ID token for audience ``cfg.services.api_url``
  (Cloud Run IAM; the API then checks the caller is the nurse assistant's service
  account). Tokens are minted with ``google.oauth2.id_token.fetch_id_token`` in a
  worker thread and cached until five minutes before they expire. No token is
  sent to a local API (``http://localhost`` / ``127.0.0.1``).
* Sends ``X-Acting-User``: the signed-in Gemini Enterprise user's email when it is
  known, so writes and PHI reads are attributable; otherwise a fixed marker that
  says the user was not verified.
* Every request has a timeout. List reads ask for the API's maximum page and
  report whether the result may be truncated (review A-1).

Only the routes the dashboard itself uses are here. Deleting call records and
resetting the worklist are deliberately absent: neither is a nurse's task in chat.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from . import turn

log = logging.getLogger(__name__)

UNVERIFIED_USER = "nurse-assistant (user not verified)"
CALLS_MAX = 500  # GET /api/calls caps limit at 500
LIST_MAX = 1000  # GET /api/call-list caps limit at 1000
SMS_MAX = 500  # GET /api/sms/sessions and /callbacks cap limit at 500
_REFRESH_EARLY_S = 300


class BackendError(Exception):
    """A failed API call. ``status`` is the HTTP status when the API answered."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, ValueError, TypeError):
        return time.time() + 600  # unknown: assume short-lived


def _fetch_id_token(audience: str) -> str:
    import google.auth.transport.requests
    from google.oauth2 import id_token

    return id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


class IdTokenSource:
    """ID tokens for one audience, cached until shortly before expiry."""

    def __init__(self, audience: str, fetch=_fetch_id_token):
        self.audience = audience
        self._fetch = fetch
        self._token: str | None = None
        self._exp = 0.0
        self._lock = asyncio.Lock()

    @property
    def needed(self) -> bool:
        a = self.audience.lower()
        return bool(a) and not a.startswith(("http://localhost", "http://127.0.0.1"))

    async def token(self) -> str | None:
        if not self.needed:
            return None
        if self._token and time.time() < self._exp - _REFRESH_EARLY_S:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._exp - _REFRESH_EARLY_S:
                return self._token
            try:
                tok = await asyncio.to_thread(self._fetch, self.audience)
            except Exception as exc:
                log.warning(
                    "id token unavailable",
                    extra={"event": "nurse.backend.token_error", "error": type(exc).__name__},
                )
                raise BackendError("The assistant could not authenticate to the API.") from None
            self._token, self._exp = tok, _jwt_exp(tok)
            return tok


def q(v: str) -> str:
    return quote(v, safe="")


def follow_up_body(tier: str, reason: str | None = None, reasons: list[str] | None = None) -> dict:
    """``POST /api/calls/{id}/follow-up``: ``reasons`` replace the call's reasons
    list, ``reason`` is the note on the change; the API needs at least one."""
    body: dict = {"follow_up": tier}
    if reasons:
        body["reasons"] = list(reasons)
    if reason:
        body["reason"] = reason
    return body


class Backend:
    def __init__(
        self,
        api_url: str,
        *,
        timeout_s: float = 12.0,
        client: httpx.AsyncClient | None = None,
        tokens: IdTokenSource | None = None,
    ):
        self.base = api_url.rstrip("/")
        self.tokens = tokens or IdTokenSource(self.base)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _headers(self) -> dict[str, str]:
        t = turn.current_or_none()
        headers = {"X-Acting-User": (t.user if t and t.user else UNVERIFIED_USER)}
        tok = await self.tokens.token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        return headers

    async def request(self, method: str, path: str, **kw) -> Any:
        if not self.base:
            raise BackendError("The outreach API URL is not configured.")
        headers = {**(await self._headers()), **kw.pop("headers", {})}
        try:
            r = await self._client.request(method, f"{self.base}{path}", headers=headers, **kw)
        except httpx.TimeoutException:
            raise BackendError("The outreach API did not answer in time.") from None
        except httpx.HTTPError as e:
            raise BackendError(f"Could not reach the outreach API ({type(e).__name__}).") from None
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, list):  # FastAPI validation errors
                detail = "; ".join(str(d.get("msg", d)) for d in detail if isinstance(d, dict))
            raise BackendError(f"{r.status_code}: {detail or r.reason_phrase}", r.status_code)
        return r.json()

    # --- calls -------------------------------------------------------------------

    async def list_calls(
        self, limit: int = CALLS_MAX, status: str | None = None
    ) -> tuple[list[dict], bool]:
        """(calls newest first, possibly truncated). The API has no "open follow-ups"
        query and no offset, so views work from the newest CALLS_MAX calls (filtered
        server-side by call status when one is asked for) and say so (A-1)."""
        limit = max(1, min(limit, CALLS_MAX))
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        calls = (await self.request("GET", "/api/calls", params=params))["calls"]
        return calls, len(calls) >= limit

    async def get_call(self, call_id: str) -> dict:
        return await self.request("GET", f"/api/calls/{q(call_id)}")

    async def get_transcript(self, call_id: str) -> dict:
        return await self.request("GET", f"/api/calls/{q(call_id)}/transcript")

    async def recording_link(self, call_id: str) -> dict:
        """A short-lived signed link to this one call's recording, served by the public
        gateway: ``{"url", "expires_at", "expires_in"}``. 404 when there is none.
        The URL is a bearer link: it goes into a card, never to the model or a log."""
        return await self.request("POST", f"/api/calls/{q(call_id)}/recording-link")

    async def place_call(self, body: dict) -> dict:
        return await self.request("POST", "/api/place-call", json=body)

    async def set_follow_up(
        self,
        call_id: str,
        tier: str,
        reason: str | None = None,
        reasons: list[str] | None = None,
    ) -> dict:
        body = follow_up_body(tier, reason, reasons)
        return await self.request("POST", f"/api/calls/{q(call_id)}/follow-up", json=body)

    # --- call list and runs ------------------------------------------------------

    async def get_call_list(self) -> list[dict]:
        return (await self.request("GET", "/api/call-list", params={"limit": LIST_MAX}))["entries"]

    async def bulk_add_entries(self, entries: list[dict]) -> dict:
        return await self.request("POST", "/api/call-list/bulk", json=entries)

    async def delete_entry(self, entry_id: str) -> dict:
        return await self.request("DELETE", f"/api/call-list/{q(entry_id)}")

    async def get_run(self) -> dict:
        return await self.request("GET", "/api/call-list/run")

    async def start_run(self, entry_ids: list[str] | None) -> dict:
        return await self.request("POST", "/api/call-list/run", json={"entry_ids": entry_ids})

    async def cancel_entries(self, entry_ids: list[str], reason: str) -> dict:
        body = {"entry_ids": entry_ids, "reason": reason}
        return await self.request("POST", "/api/call-list/cancel", json=body)

    async def reschedule_entry(
        self, entry_id: str, run_id: str | None = None, scheduled_for: str | None = None
    ) -> dict:
        body = {"run_id": run_id} if run_id else {"scheduled_for": scheduled_for}
        return await self.request("POST", f"/api/call-list/{q(entry_id)}/reschedule", json=body)

    async def get_call_runs(self) -> list[dict]:
        return (await self.request("GET", "/api/call-runs"))["runs"]

    async def get_call_settings(self) -> dict:
        return await self.request("GET", "/api/call-settings")

    # --- allowlist ---------------------------------------------------------------

    async def get_allowlist(self) -> dict:
        return await self.request("GET", "/api/allowlist")

    async def add_allowlist(self, phone: str, label: str) -> dict:
        return await self.request("POST", "/api/allowlist", json={"phone": phone, "label": label})

    async def remove_allowlist(self, phone: str) -> dict:
        return await self.request("DELETE", f"/api/allowlist/{q(phone)}")

    # --- SMS follow-up -------------------------------------------------------------

    async def get_sessions(self) -> list[dict]:
        resp = await self.request("GET", "/api/sms/sessions", params={"limit": SMS_MAX})
        return resp["sessions"]

    async def get_callbacks(self) -> list[dict]:
        resp = await self.request("GET", "/api/sms/callbacks", params={"limit": SMS_MAX})
        return resp["callbacks"]

    async def cancel_callback(self, callback_id: str) -> dict:
        return await self.request("DELETE", f"/api/sms/callbacks/{q(callback_id)}")

    async def get_messages(self, phone: str, call_sid: str | None = None) -> dict:
        params = {"phone": phone}
        if call_sid:
            params["call_sid"] = call_sid
        return await self.request("GET", "/api/sms/messages", params=params)

    async def get_sms_settings(self) -> dict:
        return (await self.request("GET", "/api/sms/settings"))["settings"]

    async def set_sms_setting(self, name: str, value: bool) -> dict:
        body = {"name": name, "value": value}
        return (await self.request("POST", "/api/sms/settings", json=body))["settings"]
