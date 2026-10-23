"""The FHIR client the exporter talks to: one small async interface.

The exporter depends on :class:`FhirClient` only. Tests inject
:class:`voa_ehr.fake_fhir.FakeFhirServer`; production uses
:class:`HttpFhirClient` (httpx), whose transport is injectable too, so even the
HTTP layer is tested with no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .profile import Profile

FHIR_JSON = "application/fhir+json"


@dataclass
class FhirResponse:
    status: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def error_text(self, limit: int = 300) -> str:
        """A short reason for logs and the outbox record: the HTTP status and the
        OperationOutcome's codes. Never the request body (it may hold PHI)."""
        parts = [f"HTTP {self.status}"]
        body = self.body or {}
        if body.get("resourceType") == "OperationOutcome":
            for iss in body.get("issue") or []:
                parts.append(f"{iss.get('severity', '')}/{iss.get('code', '')}")
        return " ".join(parts)[:limit]


class FhirClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FhirResponse:
        """``url`` is relative to the FHIR base ("Patient?identifier=...",
        "Task/12", "" for a transaction), query already encoded."""
        ...


class HttpFhirClient:
    """httpx client for a real FHIR server. ``transport`` is for tests."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        headers = {"Accept": FHIR_JSON}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
        )

    async def request(self, method, url, *, body=None, headers=None):
        h = dict(headers or {})
        content = None
        if body is not None:
            h["Content-Type"] = FHIR_JSON
            content = json.dumps(body)
        r = await self._client.request(method, url.lstrip("/"), content=content, headers=h)
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        return FhirResponse(
            r.status_code, data if isinstance(data, dict) else None, dict(r.headers)
        )

    async def aclose(self) -> None:
        await self._client.aclose()


SecretResolver = Callable[[str], str]


def client_for(
    profile: Profile,
    resolve_secret: SecretResolver,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpFhirClient:
    """A client for the profile's target. ``resolve_secret`` turns a
    ``secret://name`` into its value (``voa_core.config.resolve_secret`` with the
    project bound, in the service)."""
    auth = profile.target.auth
    if auth.type == "none":
        token = None
    elif auth.type == "bearer":
        token = resolve_secret(auth.secret)
    else:
        raise NotImplementedError(
            f"auth type {auth.type} is planned (docs/EHR-EXPORT-PLAN.md §6) but not built yet"
        )
    return HttpFhirClient(
        profile.target.base_url,
        token=token,
        timeout_s=profile.target.timeout_s,
        transport=transport,
    )


async def check_connection(client: FhirClient) -> dict[str, Any]:
    """GET /metadata: what the console's "Test connection" shows."""
    try:
        r = await client.request("GET", "metadata")
    except httpx.HTTPError as e:
        return {"ok": False, "error": type(e).__name__}
    body = r.body or {}
    if not r.ok or body.get("resourceType") != "CapabilityStatement":
        return {"ok": False, "error": r.error_text()}
    resources = sorted(
        {
            res.get("type")
            for rest in body.get("rest") or []
            for res in rest.get("resource") or []
            if res.get("type")
        }
    )
    return {
        "ok": True,
        "fhir_version": body.get("fhirVersion"),
        "software": (body.get("software") or {}).get("name"),
        "resources": resources,
    }
