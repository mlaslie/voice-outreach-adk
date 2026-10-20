"""Audit log of configuration changes (admin console, and the nurse agent's few
configuration actions).

Written centrally by the route dependency (``routes_calls.api_of``) after a
successful write to any route the policy marks as configuration: who, when, which
route, which record. Request bodies are never stored (they can hold patient
details); phone numbers in path parameters are masked.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from voa_core.logging import mask_phone
from voa_core.store import new_id, now_iso

from .auth import ADMIN_UI, INGEST, ROUTE_POLICY, acting_user, route_key

log = logging.getLogger(__name__)

COLLECTION = "audit_log"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Clinical writes that are also worth an audit line (data removal).
EXTRA = frozenset({"POST /api/calls/batch-delete"})
#: POSTs that change nothing (Admin → Knowledge base "Try it").
READ_ONLY = frozenset({"POST /api/admin/knowledge/try"})


def audited(key: str) -> bool:
    method = key.split(" ", 1)[0]
    roles = ROUTE_POLICY.get(key, frozenset())
    if key in READ_ONLY:
        return False
    return method in WRITE_METHODS and (ADMIN_UI in roles or INGEST in roles or key in EXTRA)


def _target(params: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in params.items():
        v = str(v)
        out[k] = mask_phone(v) if k == "phone" or v.startswith("+") else v
    return out


async def record(api, request: Request) -> None:
    key = route_key(request)
    if not audited(key):
        return
    doc = {
        "at": now_iso(api.now()),
        "by": acting_user(request),
        "action": key,
        "target": _target(dict(request.path_params)),
    }
    try:
        await api.store.set(COLLECTION, new_id(), doc)
    except Exception:  # never fail the change over its audit line; the log still has it
        log.exception("audit write failed", extra={"event": "audit.write_failed", "action": key})
    log.info("configuration changed", extra={"event": "audit", "action": key, "by": doc["by"]})
