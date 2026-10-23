"""Structure checks for what we would send, before it is sent.

Two parts:

* ``fhir.resources`` (BSD-3, pydantic v2): element names, cardinality, data
  types, required elements. Its R4B models are used: for the resources this
  package emits (Bundle, QuestionnaireResponse, Task, Observation,
  DocumentReference, Communication) R4B is R4 with no structural change.
* the required status/intent/priority codes, which ``fhir.resources`` does not
  check against their value sets.

This is not profile conformance (US Core, a vendor's own rules): the EHR may
still refuse a resource. The plan's §5 covers the opt-in run against a real
test server.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

#: Required codes per resource type (FHIR R4 value sets, required binding).
_REQUIRED_CODES: dict[str, dict[str, frozenset[str]]] = {
    "QuestionnaireResponse": {
        "status": frozenset({"in-progress", "completed", "amended", "entered-in-error", "stopped"})
    },
    "Task": {
        "status": frozenset(
            {
                "draft",
                "requested",
                "received",
                "accepted",
                "rejected",
                "ready",
                "cancelled",
                "in-progress",
                "on-hold",
                "failed",
                "completed",
                "entered-in-error",
            }
        ),
        "intent": frozenset(
            {
                "unknown",
                "proposal",
                "plan",
                "order",
                "original-order",
                "reflex-order",
                "filler-order",
                "instance-order",
                "option",
            }
        ),
        "priority": frozenset({"routine", "urgent", "asap", "stat"}),
    },
    "Observation": {
        "status": frozenset(
            {
                "registered",
                "preliminary",
                "final",
                "amended",
                "corrected",
                "cancelled",
                "entered-in-error",
                "unknown",
            }
        )
    },
    "DocumentReference": {
        "status": frozenset({"current", "superseded", "entered-in-error"}),
        "docStatus": frozenset({"preliminary", "final", "amended", "entered-in-error"}),
    },
    "Communication": {
        "status": frozenset(
            {
                "preparation",
                "in-progress",
                "not-done",
                "on-hold",
                "stopped",
                "completed",
                "entered-in-error",
                "unknown",
            }
        )
    },
}


def _codes(res: Mapping[str, Any], where: str, at: str = "") -> list[tuple[str, str]]:
    out = []
    for field, allowed in _REQUIRED_CODES.get(str(res.get("resourceType")), {}).items():
        v = res.get(field)
        if v is not None and v not in allowed:
            out.append((f"{at}/{field}", f"{where}.{field}: {v!r} is not a valid code"))
    return out


def _deepest(doc: Any, parts: list[Any]) -> str:
    """The JSON Pointer of the deepest node of ``parts`` that exists in ``doc``
    (a missing required element points at its parent)."""
    path = ""
    cur = doc
    for p in parts:
        if isinstance(cur, Mapping) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and isinstance(p, int) and 0 <= p < len(cur):
            cur = cur[p]
        else:
            break
        path += "/" + str(p).replace("~", "~0").replace("/", "~1")
    return path


def fhir_problem_details(resource: Mapping[str, Any]) -> list[dict[str, str]]:
    """:func:`fhir_problems` as ``[{path, message}]``; ``path`` is a JSON Pointer
    into ``resource`` (the deepest node that exists), for the console to flag."""
    from fhir.resources.R4B import get_fhir_model_class

    rtype = resource.get("resourceType")
    if not isinstance(rtype, str):
        return [{"path": "", "message": "resourceType is missing"}]
    try:
        model = get_fhir_model_class(rtype)
    except (KeyError, ValueError):
        return [{"path": "/resourceType", "message": f"unknown resourceType {rtype!r}"}]
    out: list[dict[str, str]] = []
    try:
        model.model_validate(dict(resource))
    except ValidationError as e:
        for err in e.errors():
            parts = [x for x in err["loc"] if x != "root"]
            loc = ".".join(str(x) for x in parts)
            out.append(
                {"path": _deepest(resource, parts), "message": f"{rtype}.{loc}: {err['msg']}"}
            )
    if rtype == "Bundle":
        for i, entry in enumerate(resource.get("entry") or []):
            res = (entry or {}).get("resource") or {}
            for path, msg in _codes(
                res, f"entry[{i}].{res.get('resourceType')}", f"/entry/{i}/resource"
            ):
                out.append({"path": path, "message": msg})
    else:
        for path, msg in _codes(resource, rtype):
            out.append({"path": path, "message": msg})
    return out


def fhir_problems(resource: Mapping[str, Any]) -> list[str]:
    """Problems with one resource or a Bundle (each entry checked too). [] = OK."""
    return [p["message"] for p in fhir_problem_details(resource)]
