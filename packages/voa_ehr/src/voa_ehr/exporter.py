"""The export loop: pending ``ehr_outbox`` records -> the EHR, with status write-back.

One run (:func:`run_once`) handles a batch:

* ``pending_export``: claim (a short lease, so two runs never send the same
  record at once), read the call doc for its use case and spec version, find
  the patient (``Patient?identifier=<system>|<mrn>``), build the resources
  (``transform.build_export``), check their structure, send them, write back
  ``exported`` + ``ehr_refs``. A failure counts an attempt and keeps the record
  pending until ``max_attempts``, then ``failed``.
* ``withdrawn`` (the API reopened the review): every resource the mapping could
  have sent is searched by its identifier and marked ``entered-in-error``; the
  record becomes ``withdrawn_done``. Nothing found (never exported) is fine.

State machine (``status`` on the record)::

    pending_export --> exported
          |   \\--> skipped   (no mapping for the use case; patient not found + skip)
          |    \\-> failed    (max attempts, or patient not found + fail, or invalid FHIR)
    (API) withdrawn --> withdrawn_done

Write-back is a transaction that re-reads the record: if the API withdrew or
re-signed it while we were sending (``created_at`` changed), our result is not
written over theirs; the next run sees their state.

The date of birth is read from the call doc ONLY when the profile searches with
it; the transcript is read ONLY when the profile sends it.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from voa_core.store import DocStore, Where, now_iso

from . import transform
from .client import FhirClient, FhirResponse
from .fhir_check import fhir_problem_details, fhir_problems
from .profile import Profile, UseCaseMap

log = logging.getLogger(__name__)

OUTBOX, CALLS, TRANSCRIPTS = "ehr_outbox", "calls", "transcripts"
PENDING, EXPORTED, FAILED, SKIPPED = "pending_export", "exported", "failed", "skipped"
WITHDRAWN, WITHDRAWN_DONE = "withdrawn", "withdrawn_done"

Doc = dict[str, Any]


class ExportError(Exception):
    """A failure worth retrying (or, with ``final``, not)."""

    def __init__(self, message: str, *, final: bool = False, code: str = "error"):
        super().__init__(message)
        self.final = final
        self.code = code


@dataclass
class Outcome:
    call_id: str
    status: str
    refs: list[str] = field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- patient


async def find_patient(
    client: FhirClient, profile: Profile, mrn: str, dob: str | None = None
) -> tuple[str, str | None]:
    """("found", id) | ("not_found", None) | ("ambiguous", None). Never guesses."""
    url = f"Patient?identifier={transform.token(profile.patient.identifier_system, mrn)}"
    if dob and profile.patient.search_with_birthdate:
        url += f"&birthdate={dob}"
    r = await client.request("GET", url)
    if not r.ok:
        raise ExportError(f"patient search: {r.error_text()}")
    hits = [e.get("resource") or {} for e in (r.body or {}).get("entry") or []]
    hits = [h for h in hits if h.get("resourceType") == "Patient" and h.get("id")]
    if not hits:
        return "not_found", None
    if len(hits) > 1:
        return "ambiguous", None
    return "found", str(hits[0]["id"])


# --------------------------------------------------------------------------- sending


def _ref_from(resp: FhirResponse, rtype: str) -> str | None:
    loc = resp.headers.get("Location") or resp.headers.get("location") or ""
    if loc:
        parts = [p for p in loc.split("/") if p]
        if rtype in parts:
            i = parts.index(rtype)
            if i + 1 < len(parts):
                return f"{rtype}/{parts[i + 1]}"
    body = resp.body or {}
    if body.get("resourceType") == rtype and body.get("id"):
        return f"{rtype}/{body['id']}"
    return None


def _swap(obj: Any, refs: Mapping[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _swap(v, refs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_swap(v, refs) for v in obj]
    if isinstance(obj, str) and obj in refs:
        return refs[obj]
    return obj


async def send(client: FhirClient, profile: Profile, export: transform.ExportBundle) -> list[str]:
    """Send one call's resources; the server refs ("Task/12") in entry order."""
    if not export.entries:
        return []
    if profile.target.submit == "transaction":
        r = await client.request("POST", "", body=export.bundle)
        if not r.ok:
            raise ExportError(f"transaction: {r.error_text()}", final=r.status in (400, 422))
        out = []
        for e, resp in zip(export.entries, (r.body or {}).get("entry") or [], strict=False):
            loc = (resp.get("response") or {}).get("location") or ""
            out.append(
                _ref_from(FhirResponse(200, None, {"Location": loc}), e.resource_type) or loc
            )
        return out
    # One request per resource, in order: the QuestionnaireResponse first, so the
    # Task and Observations can point at its server id.
    refs: dict[str, str] = {}
    out = []
    for e, entry in zip(export.entries, export.bundle["entry"], strict=True):
        req = entry["request"]
        body = _swap(e.resource, refs)
        headers = {"If-None-Exist": req["ifNoneExist"]} if req.get("ifNoneExist") else None
        r = await client.request(req["method"], req["url"], body=body, headers=headers)
        if not r.ok:
            raise ExportError(f"{e.resource_type}: {r.error_text()}", final=r.status in (400, 422))
        ref = _ref_from(r, e.resource_type)
        if ref:
            refs[e.full_url] = ref
            out.append(ref)
    return out


async def withdraw(
    client: FhirClient,
    profile: Profile,
    uc: UseCaseMap | None,
    call_id: str,
    known_refs: list[str],
) -> list[str]:
    """Mark everything this call sent ``entered-in-error``; the refs marked."""
    found: dict[str, Doc] = {}
    targets = transform.identifier_values(call_id, uc) if uc else []
    for rtype, value in targets:
        r = await client.request(
            "GET", f"{rtype}?identifier={transform.token(profile.identifiers.system, value)}"
        )
        if not r.ok:
            raise ExportError(f"withdraw search {rtype}: {r.error_text()}")
        for e in (r.body or {}).get("entry") or []:
            res = e.get("resource") or {}
            if res.get("id"):
                found[f"{res['resourceType']}/{res['id']}"] = res
    for ref in known_refs:
        if ref in found or ref.count("/") != 1:
            continue
        r = await client.request("GET", ref)
        if r.ok and r.body:
            found[ref] = r.body
    marked = []
    for ref, res in found.items():
        if res.get("status") == transform.ENTERED_IN_ERROR:
            marked.append(ref)
            continue
        body = transform.entered_in_error({k: v for k, v in res.items() if k != "meta"})
        r = await client.request("PUT", ref, body=body)
        if not r.ok:
            raise ExportError(f"withdraw {ref}: {r.error_text()}")
        marked.append(ref)
    return marked


# --------------------------------------------------------------------------- the loop


def _iso(dt: datetime) -> str:
    return now_iso(dt)


async def _claim(store: DocStore, call_id: str, now: datetime, lease_s: int) -> Doc | None:
    """Take the lease on a record still in a state we handle. None = not ours."""
    taken: dict[str, Doc] = {}

    def fn(cur: Doc | None) -> Doc | None:
        if not cur or cur.get("status") not in (PENDING, WITHDRAWN):
            return None
        lease = cur.get("export_lease_until")
        if lease and lease > _iso(now):
            return None
        new = {**cur, "export_lease_until": _iso(now + timedelta(seconds=lease_s))}
        taken["doc"] = new
        return new

    await store.transact(OUTBOX, call_id, fn)
    return taken.get("doc")


async def _finish(store: DocStore, call_id: str, claimed: Doc, patch: Callable[[Doc], Doc]) -> bool:
    """Write ``patch(current)`` if the record is the one we claimed (same status
    and created_at); always drop our lease. True if the result was written."""
    wrote: dict[str, bool] = {}

    def fn(cur: Doc | None) -> Doc | None:
        if cur is None:
            return None
        base = {k: v for k, v in cur.items() if k != "export_lease_until"}
        same = cur.get("status") == claimed.get("status") and cur.get("created_at") == claimed.get(
            "created_at"
        )
        if not same:
            return base
        wrote["ok"] = True
        return patch(base)

    await store.transact(OUTBOX, call_id, fn)
    return bool(wrote.get("ok"))


def _use_case(profile: Profile, record: Mapping, call: Mapping | None) -> UseCaseMap | None:
    call = call or {}
    cid = call.get("campaign_id") or record.get("campaign_id") or "post_op"
    ver = call.get("spec_version") or record.get("spec_version")
    return profile.for_use_case(cid, ver)


#: The mapping a record uses (the call doc's use case and spec version).
use_case_for = _use_case


async def _export_one(
    store: DocStore,
    client: FhirClient,
    profile: Profile,
    call_id: str,
    rec: Doc,
    now: datetime,
    max_attempts: int,
) -> Outcome:
    call = await store.get(CALLS, call_id)
    uc = _use_case(profile, rec, call)
    stamp = {"export_profile": {"id": profile.id, "version": profile.version}}

    def fail(err: ExportError) -> Outcome:
        attempts = int(rec.get("export_attempts") or 0) + 1
        status = FAILED if err.final or attempts >= max_attempts else PENDING
        return Outcome(call_id, status, error=f"{err.code}: {err}"[:300])

    if uc is None:
        out = Outcome(
            call_id, SKIPPED, error="no_mapping: the profile has no mapping for this use case"
        )
    else:
        try:
            mrn = (rec.get("patient") or {}).get("mrn")
            if not mrn:
                raise ExportError("the call has no MRN", code="no_mrn", final=True)
            dob = None
            if profile.patient.search_with_birthdate:
                dob = (call or {}).get("patient_dob")
            found, pid = await find_patient(client, profile, str(mrn), dob)
            if found != "found":
                if found == "not_found" and profile.patient.not_found == "skip":
                    raise ExportError("patient not found", code="patient_not_found")
                raise ExportError(
                    f"patient {found.replace('_', ' ')}",
                    code=f"patient_{found}",
                    final=found == "ambiguous" or profile.patient.not_found == "fail",
                )
            transcript = None
            if uc.transcript.enabled:
                transcript = ((await store.get(TRANSCRIPTS, call_id)) or {}).get("turns")
            export = transform.build_export(
                rec,
                profile,
                uc,
                transform.patient_reference(str(pid), rec, profile),
                call_id=call_id,
                call=call,
                transcript=transcript,
            )
            if profile.validate_output:
                problems = fhir_problems(export.bundle)
                if problems:
                    raise ExportError(
                        "invalid FHIR: " + "; ".join(problems), code="invalid_fhir", final=True
                    )
            refs = await send(client, profile, export)
            out = Outcome(call_id, EXPORTED, refs=refs)
        except ExportError as e:
            if e.code == "patient_not_found" and profile.patient.not_found == "skip":
                out = Outcome(call_id, SKIPPED, error="patient_not_found")
            else:
                out = fail(e)
        except Exception as e:  # network errors etc.: retried
            log.warning("ehr_export.error", extra={"call_id": call_id, "error": type(e).__name__})
            out = fail(ExportError(type(e).__name__, code="transport"))

    def patch(cur: Doc) -> Doc:
        new = {**cur, **stamp, "export_last_try_at": _iso(now)}
        if out.status == EXPORTED:
            new.update(status=EXPORTED, exported_at=_iso(now), ehr_refs=out.refs, last_error=None)
            new["export_attempts"] = int(cur.get("export_attempts") or 0) + 1
        else:
            new.update(status=out.status, last_error=out.error)
            new["export_attempts"] = int(cur.get("export_attempts") or 0) + 1
        return new

    await _finish(store, call_id, rec, patch)
    return out


async def _withdraw_one(
    store: DocStore, client: FhirClient, profile: Profile, call_id: str, rec: Doc, now: datetime
) -> Outcome:
    call = await store.get(CALLS, call_id)
    uc = _use_case(profile, rec, call)
    try:
        marked = await withdraw(client, profile, uc, call_id, list(rec.get("ehr_refs") or []))
        out = Outcome(call_id, WITHDRAWN_DONE, refs=marked)
    except ExportError as e:
        out = Outcome(call_id, WITHDRAWN, error=str(e)[:300])
    except Exception as e:
        out = Outcome(call_id, WITHDRAWN, error=type(e).__name__)

    def patch(cur: Doc) -> Doc:
        if out.status == WITHDRAWN_DONE:
            return {
                **cur,
                "status": WITHDRAWN_DONE,
                "withdrawal_sent_at": _iso(now),
                "entered_in_error": out.refs,
                "last_error": None,
            }
        return {
            **cur,
            "last_error": out.error,
            "export_attempts": int(cur.get("export_attempts") or 0) + 1,
        }

    await _finish(store, call_id, rec, patch)
    return out


async def run_once(
    store: DocStore,
    profile: Profile,
    client: FhirClient,
    *,
    now: datetime | None = None,
    limit: int = 50,
    max_attempts: int = 5,
    lease_s: int = 300,
) -> list[Outcome]:
    """One pass over the outbox. Safe to run concurrently and to re-run."""
    now = now or datetime.now(UTC)
    rows = await store.query(OUTBOX, [Where("status", "in", [PENDING, WITHDRAWN])], limit=limit)
    out: list[Outcome] = []
    for call_id, _ in rows:
        rec = await _claim(store, call_id, now, lease_s)
        if rec is None:
            continue
        if rec["status"] == WITHDRAWN:
            out.append(await _withdraw_one(store, client, profile, call_id, rec, now))
        else:
            out.append(await _export_one(store, client, profile, call_id, rec, now, max_attempts))
        log.info(
            "ehr_export.record",
            extra={"call_id": call_id, "export_status": out[-1].status, "refs": len(out[-1].refs)},
        )
    return out


def _scrub(obj: Any) -> Any:
    """A copy with every NEVER_SEND key (the DOB, under any of its names) removed,
    at any depth. Lists keep their length, so source pointers still resolve."""
    if isinstance(obj, Mapping):
        return {k: _scrub(v) for k, v in obj.items() if str(k).lower() not in transform.NEVER_SEND}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _skipped_rows(
    record: Mapping[str, Any], uc: UseCaseMap, export: transform.ExportBundle
) -> list[dict[str, Any]]:
    """[{question_id, reason, source}]: the engine's skipped answers, plus the
    excluded ones, each with its answer's pointer in the record (None when the
    answer came from the call doc)."""
    srcs = transform.answer_sources(record)
    rows = [
        {"question_id": s["question"], "reason": s["reason"], "source": srcs.get(s["question"])}
        for s in export.skipped
    ]
    qr = uc.questionnaire_response
    if qr is not None and qr.enabled:
        answers = transform.answers_from(record)
        for qid in qr.exclude:
            if qid in answers and transform.answered(answers[qid]):
                rows.append(
                    {
                        "question_id": qid,
                        "reason": "excluded by the profile",
                        "source": srcs.get(qid),
                    }
                )
    return rows


async def preview(
    store: DocStore | None,
    profile: Profile,
    record: Doc,
    *,
    call_id: str,
    call: Doc | None = None,
    patient_id: str = "example-patient",
) -> dict[str, Any]:
    """What the console's Preview shows (docs/EHR-EXPORT-PLAN.md §4.2b)::

        {ok, source_record, bundle, source_map, skipped, warnings, errors}

    ``bundle`` is byte-identical to what would be sent; ``source_map`` links each
    mapped value to the record (JSON Pointers both sides); ``errors`` are FHIR
    structure problems ``{path, message}``, ``path`` a pointer into the bundle.
    ``source_record`` is the record with any DOB removed. No FHIR server is
    contacted; the patient reference is a placeholder."""
    if call is None and store is not None:
        call = await store.get(CALLS, call_id)
    source_record = _scrub(record)
    uc = _use_case(profile, record, call)
    if uc is None:
        return {
            "ok": False,
            "source_record": source_record,
            "bundle": None,
            "source_map": [],
            "skipped": [],
            "warnings": [],
            "errors": [{"message": "the profile has no mapping for this use case"}],
        }
    transcript = None
    if uc.transcript.enabled and store is not None:
        transcript = ((await store.get(TRANSCRIPTS, call_id)) or {}).get("turns")
    export = transform.build_export(
        record,
        profile,
        uc,
        transform.patient_reference(patient_id, record, profile),
        call_id=call_id,
        call=call,
        transcript=transcript,
    )
    errors = fhir_problem_details(export.bundle)
    return {
        "ok": not errors,
        "source_record": source_record,
        "bundle": export.bundle,
        "source_map": export.source_map,
        "skipped": _skipped_rows(record, uc, export),
        "warnings": export.warnings,
        "errors": errors,
    }


class _Recorder:
    """A FhirClient that logs (method, url, status) of every request it passes on."""

    def __init__(self, inner: FhirClient):
        self.inner = inner
        self.requests: list[dict[str, Any]] = []

    async def request(self, method, url, *, body=None, headers=None) -> FhirResponse:
        r = await self.inner.request(method, url, body=body, headers=headers)
        self.requests.append({"method": method, "url": url or "(transaction)", "status": r.status})
        return r


def _versions(server: Any) -> dict[str, str]:
    return {
        f"{t}/{rid}": str((res.get("meta") or {}).get("versionId"))
        for t, rows in server.data.items()
        if t != "Patient"
        for rid, res in rows.items()
    }


async def simulate(
    profile: Profile,
    record: Doc,
    *,
    call_id: str,
    call: Doc | None = None,
    transcript: list[Doc] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the real export loop twice against the in-memory fake FHIR server,
    with an in-memory copy of the record (§4.2 "Simulate export"). The second
    run re-sends the same record, as a retry or a re-sign-off would: it should
    create nothing. No network; nothing real is stored.

    ``{runs: [{requests: [{method, url, status}], created, updated, created_refs,
    updated_refs, outcome: {status, error, refs}}], resources: [...]}``
    """
    from voa_core.store import MemoryStore

    from .fake_fhir import FakeFhirServer

    now = now or datetime.now(UTC)
    store = MemoryStore()
    rec = {
        k: v
        for k, v in copy.deepcopy(record).items()
        if k not in ("export_lease_until", "last_error", "export_attempts", "ehr_refs")
    }
    rec["status"] = PENDING
    await store.set(OUTBOX, call_id, rec)
    if call is not None:
        # The DOB never enters the simulation: the fake patient has none either.
        await store.set(CALLS, call_id, _scrub(call))
    if transcript is not None:
        await store.set(TRANSCRIPTS, call_id, {"turns": transcript})
    server = FakeFhirServer()
    mrn = (record.get("patient") or {}).get("mrn")
    if mrn:
        server.add_patient(str(mrn), profile.patient.identifier_system)
    runs = []
    for i in range(2):
        if i:
            await store.merge(OUTBOX, call_id, {"status": PENDING})
        before = _versions(server)
        client = _Recorder(server)
        outcomes = await run_once(store, profile, client, now=now, max_attempts=1)
        after = _versions(server)
        created = sorted(r for r in after if r not in before)
        updated = sorted(r for r in after if r in before and after[r] != before[r])
        out = outcomes[0] if outcomes else None
        runs.append(
            {
                "requests": client.requests,
                "created": len(created),
                "updated": len(updated),
                "created_refs": created,
                "updated_refs": updated,
                "outcome": {
                    "status": out.status if out else None,
                    "error": out.error if out else None,
                    "refs": out.refs if out else [],
                },
            }
        )
    resources = [
        copy.deepcopy(res)
        for t, rows in server.data.items()
        if t != "Patient"
        for res in rows.values()
    ]
    return {"runs": runs, "resources": resources}
