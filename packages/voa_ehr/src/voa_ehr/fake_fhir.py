"""An in-memory FHIR R4 server, just enough for the exporter's tests.

Implements :class:`voa_ehr.client.FhirClient` directly, and as an httpx
transport (``transport()``) so ``HttpFhirClient`` can be tested end to end with
no network. Supports:

* ``GET metadata``;
* search ``GET Type?identifier=system|value`` (Patient also ``birthdate``);
* conditional update ``PUT Type?identifier=...`` (0 matches: create; 1: update;
  more: 412), conditional create ``POST Type`` + ``If-None-Exist``;
* ``GET/PUT Type/id``;
* a transaction ``POST`` of a Bundle (all or nothing, urn:uuid references
  rewritten), unless ``transactions=False``;
* injected failures (``fail_next``), a request log (``log``) for assertions,
  and ``strict`` structure checks (422 on an invalid resource).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from .client import FhirResponse
from .fhir_check import fhir_problems

Doc = dict[str, Any]


def _outcome(code: str, text: str) -> Doc:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": code, "diagnostics": text}],
    }


def _idents(res: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    i = res.get("identifier")
    if isinstance(i, Mapping):
        return [i]
    return list(i or [])


def _token_match(res: Mapping[str, Any], tok: str) -> bool:
    system, sep, value = tok.partition("|")
    if not sep:
        system, value = "", tok
    return any(
        (not system or i.get("system") == system) and i.get("value") == value for i in _idents(res)
    )


class _Fail(Exception):
    def __init__(self, status: int, code: str, text: str):
        self.status, self.code, self.text = status, code, text


class FakeFhirServer:
    def __init__(self, *, transactions: bool = True, strict: bool = True):
        self.data: dict[str, dict[str, Doc]] = {}
        self.log: list[dict[str, Any]] = []
        self.transactions = transactions
        self.strict = strict
        self._next_id = 0
        #: Statuses to return for the next requests, in order (e.g. [503]).
        self.fail_next: list[int] = []

    # ------------------------------------------------------------------ helpers

    def add_patient(self, mrn: str, system: str, *, birth_date: str | None = None) -> str:
        pid = self._new_id()
        res: Doc = {
            "resourceType": "Patient",
            "id": pid,
            "identifier": [{"system": system, "value": mrn}],
        }
        if birth_date:
            res["birthDate"] = birth_date
        self.data.setdefault("Patient", {})[pid] = res
        return pid

    def all(self, rtype: str) -> list[Doc]:
        return list(self.data.get(rtype, {}).values())

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _search(self, rtype: str, query: Mapping[str, list[str]]) -> list[Doc]:
        rows = list(self.data.get(rtype, {}).values())
        for k, vals in query.items():
            if k == "identifier":
                rows = [r for r in rows if all(_token_match(r, v) for v in vals)]
            elif k == "birthdate" and rtype == "Patient":
                rows = [r for r in rows if r.get("birthDate") in vals]
            elif k.startswith("_"):
                continue
            else:
                raise _Fail(400, "not-supported", f"search parameter {k} not supported")
        return rows

    def _check(self, res: Mapping[str, Any], rtype: str) -> None:
        if res.get("resourceType") != rtype:
            raise _Fail(400, "invalid", "resourceType does not match the URL")
        if self.strict:
            problems = fhir_problems(res)
            if problems:
                raise _Fail(422, "structure", "; ".join(problems)[:500])

    def _write(self, rtype: str, rid: str, res: Mapping[str, Any]) -> tuple[int, Doc]:
        table = self.data.setdefault(rtype, {})
        prev = table.get(rid)
        version = int((prev or {}).get("meta", {}).get("versionId", "0")) + 1
        doc = {**copy.deepcopy(dict(res)), "id": rid, "meta": {"versionId": str(version)}}
        table[rid] = doc
        return (200 if prev else 201), doc

    def _one(self, method: str, url: str, body: Doc | None, headers: Mapping[str, str]):
        """(status, body, location) for one interaction. Raises _Fail."""
        parts = urlsplit(url.lstrip("/"))
        path = [p for p in parts.path.split("/") if p]
        query = parse_qs(parts.query)
        if path == ["metadata"] and method == "GET":
            return 200, self.capability(), None
        if not path:
            raise _Fail(400, "not-supported", "no resource type")
        rtype = path[0]
        if len(path) == 1:
            if method == "GET":
                rows = self._search(rtype, query)
                return (
                    200,
                    {
                        "resourceType": "Bundle",
                        "type": "searchset",
                        "total": len(rows),
                        "entry": [
                            {"fullUrl": f"{rtype}/{r['id']}", "resource": copy.deepcopy(r)}
                            for r in rows
                        ],
                    },
                    None,
                )
            if method == "PUT":
                if "identifier" not in query:
                    raise _Fail(400, "invalid", "conditional update needs a search")
                self._check(body or {}, rtype)
                hits = self._search(rtype, query)
                if len(hits) > 1:
                    raise _Fail(412, "multiple-matches", "more than one match")
                rid = hits[0]["id"] if hits else self._new_id()
                status, doc = self._write(rtype, rid, body or {})
                return status, doc, f"{rtype}/{rid}/_history/{doc['meta']['versionId']}"
            if method == "POST":
                self._check(body or {}, rtype)
                cond = headers.get("If-None-Exist") or headers.get("if-none-exist")
                if cond:
                    hits = self._search(rtype, parse_qs(cond))
                    if len(hits) > 1:
                        raise _Fail(412, "multiple-matches", "more than one match")
                    if hits:
                        h = hits[0]
                        return 200, copy.deepcopy(h), f"{rtype}/{h['id']}"
                status, doc = self._write(rtype, self._new_id(), body or {})
                return 201, doc, f"{rtype}/{doc['id']}/_history/1"
        if len(path) == 2:
            rid = path[1]
            if method == "GET":
                doc = self.data.get(rtype, {}).get(rid)
                if doc is None:
                    raise _Fail(404, "not-found", f"{rtype}/{rid}")
                return 200, copy.deepcopy(doc), None
            if method == "PUT":
                self._check(body or {}, rtype)
                status, doc = self._write(rtype, rid, body or {})
                return status, doc, f"{rtype}/{rid}/_history/{doc['meta']['versionId']}"
        raise _Fail(400, "not-supported", f"{method} {url}")

    def _transaction(self, bundle: Doc) -> Doc:
        if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "transaction":
            raise _Fail(400, "invalid", "expected a transaction Bundle")
        if self.strict:
            problems = fhir_problems(bundle)
            if problems:
                raise _Fail(422, "structure", "; ".join(problems)[:500])
        saved = copy.deepcopy(self.data), self._next_id
        try:
            # Pass 1: decide each entry's id, so urn:uuid references can be rewritten.
            ids: dict[str, str] = {}
            for e in bundle.get("entry") or []:
                req = e.get("request") or {}
                rtype = urlsplit(req.get("url", "")).path.split("/")[0]
                if req.get("method") == "PUT" and "?" in req.get("url", ""):
                    hits = self._search(rtype, parse_qs(urlsplit(req["url"]).query))
                    if len(hits) > 1:
                        raise _Fail(412, "multiple-matches", req["url"])
                    rid = hits[0]["id"] if hits else self._new_id()
                elif req.get("method") == "POST" and req.get("ifNoneExist"):
                    hits = self._search(rtype, parse_qs(req["ifNoneExist"]))
                    rid = hits[0]["id"] if hits else self._new_id()
                else:
                    rid = self._new_id()
                if e.get("fullUrl"):
                    ids[e["fullUrl"]] = f"{rtype}/{rid}"
                e["_rid"] = (rtype, rid)
            text = json.dumps([e.get("resource") for e in bundle.get("entry") or []])
            for urn, ref in ids.items():
                text = text.replace(f'"{urn}"', f'"{ref}"')
            resources = json.loads(text)
            out = []
            for e, res in zip(bundle.get("entry") or [], resources, strict=True):
                rtype, rid = e.pop("_rid")
                req = e.get("request") or {}
                exists = rid in self.data.get(rtype, {})
                if req.get("method") == "POST" and req.get("ifNoneExist") and exists:
                    out.append({"response": {"status": "200 OK", "location": f"{rtype}/{rid}"}})
                    continue
                self._check(res, rtype)
                status, doc = self._write(rtype, rid, res)
                loc = f"{rtype}/{rid}/_history/{doc['meta']['versionId']}"
                label = "201 Created" if status == 201 else "200 OK"
                out.append({"response": {"status": label, "location": loc}})
            return {"resourceType": "Bundle", "type": "transaction-response", "entry": out}
        except _Fail:
            self.data, self._next_id = saved
            raise

    def capability(self) -> Doc:
        types = ["Patient", "QuestionnaireResponse", "Task", "Observation"]
        types += ["DocumentReference", "Communication"]
        return {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "kind": "instance",
            "fhirVersion": "4.0.1",
            "software": {"name": "voa fake FHIR"},
            "format": ["json"],
            "rest": [
                {
                    "mode": "server",
                    "resource": [{"type": t} for t in types],
                    "interaction": [{"code": "transaction"}] if self.transactions else [],
                }
            ],
        }

    # ------------------------------------------------------------------ FhirClient

    async def request(self, method, url, *, body=None, headers=None) -> FhirResponse:
        return self.handle(method, url, body=body, headers=headers)

    def handle(self, method, url, *, body=None, headers=None) -> FhirResponse:
        body = copy.deepcopy(dict(body)) if body is not None else None
        self.log.append(
            {"method": method, "url": url, "body": body, "headers": dict(headers or {})}
        )
        if self.fail_next:
            status = self.fail_next.pop(0)
            return FhirResponse(status, _outcome("transient", "injected failure"))
        try:
            if method == "POST" and url.strip("/") == "":
                if not self.transactions:
                    raise _Fail(501, "not-supported", "transactions are not supported")
                return FhirResponse(200, self._transaction(body or {}))
            status, doc, loc = self._one(method, url, body, headers or {})
        except _Fail as f:
            return FhirResponse(f.status, _outcome(f.code, f.text))
        return FhirResponse(status, doc, {"Location": loc} if loc else {})

    def transport(self) -> httpx.MockTransport:
        """This server as an httpx transport, for ``HttpFhirClient`` tests."""

        def handler(req: httpx.Request) -> httpx.Response:
            base = "/fhir/"
            path = req.url.raw_path.decode()
            rel = path[len(base) :] if path.startswith(base) else path.lstrip("/")
            body = json.loads(req.content) if req.content else None
            r = self.handle(req.method, rel, body=body, headers=dict(req.headers))
            return httpx.Response(r.status, json=r.body, headers=r.headers)

        return httpx.MockTransport(handler)
