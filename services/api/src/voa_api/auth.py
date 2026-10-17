"""Caller identity for the private API.

Cloud Run IAM has already verified the Google ID token (the service is
``--no-allow-unauthenticated``). Here we only read *which* service account
called, to keep each route group to its intended caller:

* dashboard routes (``/api/*``): the UI's service account (it forwards the
  signed-in nurse as ``X-Acting-User``);
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


def ui_callers(request: Request) -> set[str]:
    cfg = request.app.state.api.cfg
    return {e for e in (cfg.services.ui_sa, cfg.services.nurse_agent_sa) if e}


def task_callers(request: Request) -> set[str]:
    return {request.app.state.api.cfg.services.tasks_invoker_sa}
