"""Caller identity for the private API.

Cloud Run IAM has already verified the Google ID token (the service is
``--no-allow-unauthenticated``). Here we only read *which* service account
called, to keep each route group to its intended caller:

* dashboard routes (``/api/*``): each route is in ``ROUTE_POLICY`` with the
  identities allowed to call it -- the nurse UI, the admin UI, the nurse agent --
  so configuration can only change from the admin console (docs/ADMIN-PLAN.md).
  A route missing from the table is refused. The UIs forward the signed-in user
  as ``X-Acting-User``;
* task and cron routes (``/tasks/*``, ``/cron/*``): the tasks-invoker account.

In ``dev``/``test`` (no Cloud Run in front) checks are skipped.
"""

from __future__ import annotations

import base64
import json

from fastapi import HTTPException, Request


def caller_email(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = auth.split(" ", 1)[1].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("email")
    except Exception:
        return None


def acting_user(request: Request) -> str:
    """The nurse who clicked (set by the dashboard BFF from IAP), for audit fields."""
    return (request.headers.get("x-acting-user") or "dashboard")[:200]


def require_caller(request: Request, allowed: set[str]) -> None:
    api = request.app.state.api
    if api.cfg.env in ("dev", "test"):
        return
    email = caller_email(request)
    if email not in allowed:
        raise HTTPException(403, "caller not permitted")


def task_callers(request: Request) -> set[str]:
    return {request.app.state.api.cfg.services.tasks_invoker_sa}


# --------------------------------------------------------------------------- roles

NURSE_UI, ADMIN_UI, NURSE_AGENT, INGEST = "nurse_ui", "admin_ui", "nurse_agent", "ingest"
READ = frozenset({NURSE_UI, ADMIN_UI, NURSE_AGENT})
NURSE = frozenset({NURSE_UI, NURSE_AGENT})
ADMIN = frozenset({ADMIN_UI})
# Configuration the Gemini Enterprise nurse agent can also do today, each behind a
# confirm button. Whether these move to admin-only is decided in admin plan step 2.
ADMIN_OR_AGENT = frozenset({ADMIN_UI, NURSE_AGENT})
# The EHR feed (docs/ADMIN-PLAN.md section 3): batch routes only.
FEED = frozenset({INGEST})

#: "METHOD /path/template" -> who may call it. Every dashboard route must be here
#: (tests/test_route_policy.py fails otherwise).
ROUTE_POLICY: dict[str, frozenset[str]] = {
    # calls: reading, and the clinical work on them
    "GET /api/calls": READ,
    "GET /api/follow-ups/open": READ,
    "GET /api/calls/stream": READ,
    "GET /api/calls/{call_id}": READ,
    "GET /api/calls/{call_id}/transcript": READ,
    "GET /api/calls/{call_id}/recording": READ,
    "POST /api/calls/{call_id}/follow-up": NURSE,
    "POST /api/calls/sign-off": NURSE,
    "POST /api/calls/{call_id}/sign-off": NURSE,
    "POST /api/calls/batch-delete": NURSE,
    "POST /api/calls/{call_id}/recording-link": NURSE,
    # shadow mode: a nurse rates the knowledge agent's draft answer (audited)
    "POST /api/calls/{call_id}/questions/{index}/shadow-review": NURSE,
    "POST /api/place-call": NURSE,
    "POST /api/test-call": ADMIN,  # Admin → Testing ("Test in browser")
    "GET /api/calls/{call_id}/events": ADMIN,  # Admin → Call diagnostics
    "GET /api/audit": ADMIN,  # Admin → Audit log
    # the call queue: nurses run, cancel and reschedule calls
    "GET /api/call-list": READ,
    "GET /api/call-list/run": READ,
    "POST /api/call-list/run": NURSE,
    "POST /api/call-list/run/cancel": NURSE,
    "POST /api/call-list/cancel": NURSE,
    "POST /api/call-list/{entry_id}/reschedule": NURSE,
    # patient records and schedules: configuration
    "POST /api/call-list": ADMIN,
    "POST /api/call-list/bulk": ADMIN_OR_AGENT,
    "PATCH /api/call-list/{entry_id}": ADMIN,
    "DELETE /api/call-list/{entry_id}": ADMIN_OR_AGENT,
    "POST /api/call-list/reset": ADMIN,
    "GET /api/call-runs": READ,
    "POST /api/call-runs": ADMIN,
    "PATCH /api/call-runs/{run_id}": ADMIN,
    "DELETE /api/call-runs/{run_id}": ADMIN,
    "GET /api/call-settings": READ,
    "GET /api/languages": READ,  # patient languages and which are available
    "GET /api/campaigns": READ,  # the use cases an entry may name (campaign_id)
    "PUT /api/call-settings/recall": ADMIN,
    "PUT /api/call-settings/display": ADMIN,
    "PUT /api/call-settings/organization": ADMIN,
    "GET /api/allowlist": READ,
    "POST /api/allowlist": ADMIN_OR_AGENT,
    "DELETE /api/allowlist/{phone}": ADMIN_OR_AGENT,
    # texts
    "GET /api/sms/sessions": READ,
    "GET /api/sms/callbacks": READ,
    "DELETE /api/sms/callbacks/{callback_id}": NURSE | ADMIN,
    "GET /api/sms/messages": READ,
    "GET /api/sms/consent": READ,
    "POST /api/sms/consent": ADMIN,
    "GET /api/sms/settings": READ,
    "POST /api/sms/settings": ADMIN_OR_AGENT,
    "POST /api/sms/test/start": ADMIN,
    "POST /api/sms/test/reply": ADMIN,
    # staff-console branding (admin plan step 4)
    "GET /api/branding": READ,
    "PUT /api/branding": ADMIN,
    "DELETE /api/branding": ADMIN,
    "GET /api/branding/assets/{kind}": READ,
    "PUT /api/branding/assets/{kind}": ADMIN,
    # knowledge agent (docs/KNOWLEDGE-API.md): examples, guidelines, settings, "Try it"
    "GET /api/admin/knowledge/items": ADMIN,
    "POST /api/admin/knowledge/items": ADMIN,
    "PATCH /api/admin/knowledge/items/{item_id}": ADMIN,
    "POST /api/admin/knowledge/items/{item_id}/approve": ADMIN,
    "POST /api/admin/knowledge/items/{item_id}/retire": ADMIN,
    "DELETE /api/admin/knowledge/items/{item_id}": ADMIN,
    "GET /api/admin/knowledge/documents": ADMIN,
    "POST /api/admin/knowledge/documents": ADMIN,
    "PATCH /api/admin/knowledge/documents/{doc_id}": ADMIN,
    "POST /api/admin/knowledge/documents/{doc_id}/approve": ADMIN,
    "POST /api/admin/knowledge/documents/{doc_id}/retire": ADMIN,
    "DELETE /api/admin/knowledge/documents/{doc_id}": ADMIN,
    "GET /api/admin/knowledge/settings": ADMIN,
    "PUT /api/admin/knowledge/settings": ADMIN,
    "POST /api/admin/knowledge/try": ADMIN,  # read-only: not audited (audit.READ_ONLY)
    # EHR feed (admin plan step 3): idempotent call batches
    "PUT /api/v1/call-batches/{source}/{batch_id}": FEED,
    "GET /api/v1/call-batches/{source}/{batch_id}": FEED | ADMIN,
    "DELETE /api/v1/call-batches/{source}/{batch_id}": FEED,
    "GET /api/v1/call-batches": ADMIN,
}


def _role_emails(request: Request) -> dict[str, str]:
    s = request.app.state.api.cfg.services
    return {
        NURSE_UI: s.ui_sa,
        ADMIN_UI: s.admin_ui_sa,
        NURSE_AGENT: s.nurse_agent_sa,
        INGEST: s.ingest_sa,
    }


def route_key(request: Request) -> str:
    route = request.scope.get("route")
    return f"{request.method} {getattr(route, 'path', request.url.path)}"


def authorize(request: Request) -> None:
    """Allow the call only from an identity the route's policy names. Fails closed."""
    api = request.app.state.api
    if api.cfg.env in ("dev", "test"):
        return
    roles = ROUTE_POLICY.get(route_key(request))
    if roles is None:
        raise HTTPException(403, "route has no access policy")
    emails = _role_emails(request)
    if caller_email(request) not in {emails[r] for r in roles if emails[r]}:
        raise HTTPException(403, "caller not permitted for this route")
