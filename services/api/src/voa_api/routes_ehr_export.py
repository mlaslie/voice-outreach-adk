"""Admin → Integrations → EHR export: the export profile's versions, live
validation, preview with a source map, simulate, YAML in and out, and the outbox.

The contract is docs/EHR-EXPORT-PLAN.md §4.2b. Storage (§4.4, ``voa_ehr.versions``):

* ``settings/ehr_export``: ``{active_version, draft_version, next_version}``;
* ``settings/ehr_export/versions/{n}``: ``{version, status, profile, problems,
  saved_by, saved_at, activated_by, activated_at, retired_at, note}``.

Every route is ADMIN (``auth.ROUTE_POLICY``). Writes are audited by the route
dependency (``audit.py``); validate, preview and simulate change nothing and are
not (``audit.READ_ONLY``). No route contacts a FHIR server: preview is offline
and simulate runs against the in-memory fake (``voa_ehr.fake_fhir``).

A draft is saved even when it has errors (so work is never lost); activation
refuses a profile with errors (409, with the problems). Saving stamps the
version number into the profile's own ``version``, which the exporter writes on
each record it sends (``export_profile``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from voa_core.campaign import UnknownCampaign, campaign_ids, default_config_dir, load_campaign
from voa_core.store import Where, now_iso
from voa_ehr import exporter
from voa_ehr import versions as vs
from voa_ehr.profile import (
    ENGINE_FIELDS,
    Profile,
    ProfileError,
    allowed_types,
    parse_profile,
    profile_from_text,
    profile_problems,
    profile_to_yaml,
    question_choices,
    question_kind,
)

from .auth import acting_user
from .context import CALL_ID_RE, CALLS, Api
from .routes_calls import api_of

log = logging.getLogger(__name__)

#: The profile as JSON, and an uploaded YAML document: 200 KB each.
MAX_PROFILE_BYTES = 200_000
#: The whole request body: a profile plus a little envelope.
MAX_BODY_BYTES = MAX_PROFILE_BYTES + 16_000
OUTBOX, TRANSCRIPTS = exporter.OUTBOX, exporter.TRANSCRIPTS
OUTBOX_STATUSES = frozenset(
    {
        exporter.PENDING,
        exporter.EXPORTED,
        exporter.FAILED,
        exporter.SKIPPED,
        exporter.WITHDRAWN,
        exporter.WITHDRAWN_DONE,
    }
)
NOT_REQUEUEABLE = frozenset({exporter.WITHDRAWN, exporter.WITHDRAWN_DONE})
EXAMPLE_NAME = "post_op.example.yaml"


async def _size_limit(request: Request) -> None:
    """413 for a body over the limit (checked before the body is parsed)."""
    if request.method not in ("POST", "PUT"):
        return
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(413, "The request is too large (a profile is at most 200 KB).")
    if len(await request.body()) > MAX_BODY_BYTES:
        raise HTTPException(413, "The request is too large (a profile is at most 200 KB).")


router = APIRouter(prefix="/api/ehr-export", dependencies=[Depends(api_of), Depends(_size_limit)])


# --------------------------------------------------------------------------- bodies


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftBody(_B):
    profile: dict[str, Any] | None = None
    yaml: str | None = None
    note: str | None = Field(default=None, max_length=500)


class ValidateBody(_B):
    profile: dict[str, Any] | None = None
    yaml: str | None = None


class NoteBody(_B):
    note: str | None = Field(default=None, max_length=500)


class PreviewBody(_B):
    profile: dict[str, Any] | None = None
    #: {"sample": campaign_id} | {"call_id": id}
    source: dict[str, str]


# --------------------------------------------------------------------------- helpers


def ehr_config_dir() -> Path:
    return default_config_dir() / "ehr"


def _profile_data(profile: dict[str, Any] | None, text: str | None) -> dict[str, Any]:
    """The profile from a body: JSON (``profile``) or YAML/JSON text (``yaml``)."""
    if (profile is None) == (text is None):
        raise HTTPException(422, "Send the profile as either 'profile' (JSON) or 'yaml'.")
    if text is not None:
        if len(text.encode()) > MAX_PROFILE_BYTES:
            raise HTTPException(413, "The YAML is too large (at most 200 KB).")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(422, f"Not valid YAML or JSON: {e}") from None
        if not isinstance(data, dict):
            raise HTTPException(422, "The YAML must be a mapping (the profile).")
        return data
    assert profile is not None
    if len(json.dumps(profile).encode()) > MAX_PROFILE_BYTES:
        raise HTTPException(413, "The profile is too large (at most 200 KB).")
    return profile


def _check(data: Any) -> tuple[Profile | None, list[dict[str, str]]]:
    """(the parsed profile or None, every problem as Problem.as_dict())."""
    try:
        profile = parse_profile(data)
    except ProfileError as e:
        return None, [p.as_dict() for p in e.problems]
    return profile, [p.as_dict() for p in profile_problems(profile)]


def _has_errors(problems: list[dict[str, str]]) -> bool:
    return any(p["level"] == "error" for p in problems)


def _normalise(data: dict[str, Any], n: int) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The profile to store as version ``n``: its own ``version`` set to ``n`` and,
    when it parses, in its full canonical JSON form."""
    data = {**data, "version": n}
    profile, problems = _check(data)
    if profile is not None:
        data = profile.model_dump(mode="json")
    return data, problems


def _public(doc: dict[str, Any] | None, *, with_profile: bool = True) -> dict[str, Any] | None:
    if doc is None:
        return None
    out = {
        "version": doc.get("version"),
        "status": doc.get("status"),
        "problems": doc.get("problems") or [],
        "saved_by": doc.get("saved_by"),
        "saved_at": doc.get("saved_at"),
        "activated_by": doc.get("activated_by"),
        "activated_at": doc.get("activated_at"),
        "note": doc.get("note"),
    }
    if with_profile:
        out["profile"] = doc.get("profile")
    return out


async def _fresh(api: Api, n: int | None) -> dict[str, Any] | None:
    """A version with its problems re-checked (the use-case specs may have changed)."""
    doc = await vs.get_version(api.store, n)
    if doc is None:
        return None
    _, problems = _check(doc.get("profile"))
    return _public({**doc, "problems": problems})


async def _save_draft(
    api: Api, request: Request, data: dict[str, Any], note: str | None
) -> dict[str, Any]:
    """Create or replace the draft. The draft keeps its number until activated."""
    got: dict[str, int] = {}

    def alloc(cur: dict | None) -> dict:
        cur = dict(cur or {})
        n = cur.get("draft_version")
        if n is None:
            n = int(cur.get("next_version") or 1)
            cur["next_version"] = n + 1
            cur["draft_version"] = n
        got["n"] = int(n)
        return cur

    await api.store.transact(vs.SETTINGS, vs.POINTER, alloc)
    n = got["n"]
    profile, problems = _normalise(data, n)
    doc = {
        "version": n,
        "status": vs.DRAFT,
        "profile": profile,
        "problems": problems,
        "saved_by": acting_user(request),
        "saved_at": now_iso(api.now()),
        "activated_by": None,
        "activated_at": None,
        "note": note,
    }
    await api.store.set(vs.VERSIONS, str(n), doc)
    return _public(doc) or {}


def _example() -> dict[str, Any]:
    path = ehr_config_dir() / "profiles" / EXAMPLE_NAME
    try:
        return profile_from_text(path.read_text()).model_dump(mode="json")
    except (OSError, ProfileError) as e:
        log.error("ehr example profile unreadable", extra={"error": type(e).__name__})
        raise HTTPException(500, "The example profile is missing from this deployment.") from None


async def _profile_for(api: Api, body: dict[str, Any] | None) -> tuple[Profile, str]:
    """The profile a preview or simulation uses: the body's, else the saved draft,
    else the active profile, else the example. 422 when it does not parse."""
    if body is not None:
        data, where = _profile_data(body, None), "body"
    else:
        ptr = await vs.pointer(api.store)
        data, where = None, ""
        for key, label in (("draft_version", "draft"), ("active_version", "active")):
            doc = await vs.get_version(api.store, ptr.get(key))
            if doc:
                data, where = doc.get("profile"), label
                break
        if data is None:
            data, where = _example(), "example"
    try:
        return parse_profile(data), where
    except ProfileError as e:
        raise HTTPException(
            422,
            {
                "message": f"The {where} profile is not valid.",
                "problems": [p.as_dict() for p in e.problems],
            },
        ) from None


def _samples() -> list[tuple[str, Path]]:
    d = ehr_config_dir() / "samples"
    return [(p.name.split(".")[0], p) for p in sorted(d.glob("*.outbox.json"))]


def _label(record: dict[str, Any]) -> str:
    """First name and last initial ("Jordan R."), as the console's lists show."""
    name = " ".join(str((record.get("patient") or {}).get("name") or "").split())
    if not name:
        return "(no name)"
    parts = name.split(" ")
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


async def _source(
    api: Api, src: dict[str, str]
) -> tuple[str, dict[str, Any], dict[str, Any] | None, bool]:
    """(call_id, record, call doc, stored) for a preview source."""
    if set(src) == {"sample"}:
        for cid, path in _samples():
            if cid == src["sample"]:
                data = json.loads(path.read_text())
                return (
                    str(data.get("call_id") or "example-call"),
                    data["record"],
                    data.get("call"),
                    False,
                )
        raise HTTPException(404, "No fictional sample for that use case.")
    if set(src) == {"call_id"}:
        call_id = src["call_id"]
        if not CALL_ID_RE.match(call_id):
            raise HTTPException(400, "invalid call id")
        rec = await api.store.get(OUTBOX, call_id)
        if not rec:
            raise HTTPException(404, "No outbox record for that call.")
        return call_id, rec, await api.store.get(CALLS, call_id), True
    raise HTTPException(422, "source must be {sample: <use case>} or {call_id: <id>}.")


def _outbox_row(call_id: str, rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "status": rec.get("status"),
        "created_at": rec.get("created_at"),
        "exported_at": rec.get("exported_at"),
        "last_error": rec.get("last_error"),
        "attempts": rec.get("export_attempts"),
        "label": _label(rec),
    }


# --------------------------------------------------------------------------- versions


@router.get("")
async def get_state(request: Request):
    api: Api = request.app.state.api
    ptr = await vs.pointer(api.store)
    return {
        "active_version": ptr.get("active_version"),
        "draft_version": ptr.get("draft_version"),
        "active": await _fresh(api, ptr.get("active_version")),
        "draft": await _fresh(api, ptr.get("draft_version")),
        # Not in §4.2b: the use cases a mapping can name ("Add use case").
        "use_cases": campaign_ids(),
    }


@router.get("/questions")
async def questions(campaign_id: str = Query(..., max_length=64)):
    try:
        campaign = load_campaign(campaign_id)
    except (UnknownCampaign, OSError, ValueError):
        raise HTTPException(404, "No such use case.") from None
    out = []
    for q in campaign.questions:
        kind = question_kind(campaign, q.id) or "text"
        out.append(
            {
                "id": q.id,
                "ask": q.ask,
                "type": q.kind,
                "parser": q.parser or None,
                "choices": question_choices(campaign, q.id) or list(q.choices),
                "allowed_types": sorted(allowed_types(kind)),
                "engine": False,
            }
        )
    engine = []
    if campaign.has_open_questions:
        engine.append(("patient_questions", "The patient's own questions (a list)"))
    if any(q.parser == "blood_sugar" for q in campaign.questions):
        engine.append(("blood_sugar_reading", "The blood sugar, in the patient's words"))
    for fid, ask in engine:
        out.append(
            {
                "id": fid,
                "ask": ask,
                "type": ENGINE_FIELDS[fid],
                "parser": None,
                "choices": [],
                "allowed_types": sorted(allowed_types(ENGINE_FIELDS[fid])),
                "engine": True,
            }
        )
    return {"campaign_id": campaign.id, "spec_version": campaign.version, "questions": out}


@router.post("/validate")
async def validate(body: ValidateBody):
    _, problems = _check(_profile_data(body.profile, body.yaml))
    return {"problems": problems}


@router.put("/draft")
async def save_draft(body: DraftBody, request: Request):
    api: Api = request.app.state.api
    return await _save_draft(api, request, _profile_data(body.profile, body.yaml), body.note)


@router.post("/draft/reset")
async def reset_draft(request: Request):
    api: Api = request.app.state.api
    ptr = await vs.pointer(api.store)
    active = await vs.get_version(api.store, ptr.get("active_version"))
    if not active:
        raise HTTPException(409, "There is no active version to reset to.")
    return await _save_draft(
        api, request, active["profile"], f"reset to active version {active['version']}"
    )


@router.post("/activate")
async def activate(request: Request, body: NoteBody | None = None):
    api: Api = request.app.state.api
    ptr = await vs.pointer(api.store)
    n = ptr.get("draft_version")
    draft = await vs.get_version(api.store, n)
    if not draft:
        raise HTTPException(409, "There is no draft to activate.")
    _, problems = _check(draft.get("profile"))
    if _has_errors(problems):
        raise HTTPException(
            409, {"message": "The draft has errors: fix them first.", "problems": problems}
        )
    now = now_iso(api.now())
    old = ptr.get("active_version")
    if old is not None and old != n:
        await api.store.merge(vs.VERSIONS, str(old), {"status": vs.RETIRED, "retired_at": now})
    note = body.note if body and body.note is not None else draft.get("note")
    done = {
        **draft,
        "status": vs.ACTIVE,
        "problems": problems,
        "activated_by": acting_user(request),
        "activated_at": now,
        "note": note,
    }
    await api.store.set(vs.VERSIONS, str(n), done)

    def point(cur: dict | None) -> dict:
        cur = dict(cur or {})
        cur["active_version"] = n
        if cur.get("draft_version") == n:
            cur["draft_version"] = None
        return cur

    await api.store.transact(vs.SETTINGS, vs.POINTER, point)
    log.info("ehr export profile activated", extra={"event": "ehr_export.activate", "version": n})
    return _public(done)


@router.get("/versions")
async def list_versions(request: Request):
    api: Api = request.app.state.api
    rows = await api.store.query(vs.VERSIONS)
    docs = sorted((d for _, d in rows), key=lambda d: int(d.get("version") or 0), reverse=True)
    return {"versions": [_public(d, with_profile=False) for d in docs]}


@router.post("/versions/{n}/restore")
async def restore(n: int, request: Request):
    api: Api = request.app.state.api
    doc = await vs.get_version(api.store, n)
    if not doc:
        raise HTTPException(404, "No such version.")
    return await _save_draft(api, request, doc["profile"], f"restored from version {n}")


@router.get("/yaml")
async def download_yaml(request: Request, version: str = Query("active", max_length=12)):
    api: Api = request.app.state.api
    ptr = await vs.pointer(api.store)
    if version in ("draft", "active"):
        n = ptr.get(f"{version}_version")
    elif version.isdigit():
        n = int(version)
    else:
        raise HTTPException(422, "version is draft, active or a number.")
    doc = await vs.get_version(api.store, n)
    if not doc:
        raise HTTPException(404, f"No {version} version.")
    data = doc.get("profile") or {}
    try:
        text = profile_to_yaml(parse_profile(data))
    except ProfileError:  # a draft with model errors: as saved
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    pid = str(data.get("id") or "ehr-export")
    name = "".join(c for c in pid if c.isalnum() or c in "._-") or "ehr-export"
    return Response(
        text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}.v{doc["version"]}.yaml"'},
    )


@router.get("/example")
async def example():
    return {"profile": _example()}


# --------------------------------------------------------------------------- preview


@router.get("/preview-sources")
async def preview_sources(request: Request):
    api: Api = request.app.state.api
    samples = []
    for cid, _path in _samples():
        try:
            name = load_campaign(cid).name
        except (UnknownCampaign, OSError, ValueError):
            name = cid
        samples.append({"campaign_id": cid, "label": f"Fictional sample: {name}"})
    rows = await api.store.query(OUTBOX, order_by="created_at", descending=True, limit=20)
    calls = []
    for call_id, rec in rows:
        cid = rec.get("campaign_id")
        if not cid:
            cid = ((await api.store.get(CALLS, call_id)) or {}).get("campaign_id")
        calls.append(
            {
                "call_id": call_id,
                "campaign_id": cid,
                "signed_off_at": (rec.get("review") or {}).get("at") or rec.get("created_at"),
                "status": rec.get("status"),
                "label": _label(rec),
            }
        )
    return {"samples": samples, "calls": calls}


@router.post("/preview")
async def preview(body: PreviewBody, request: Request):
    api: Api = request.app.state.api
    profile, where = await _profile_for(api, body.profile)
    call_id, record, call, stored = await _source(api, body.source)
    out = await exporter.preview(
        api.store if stored else None, profile, record, call_id=call_id, call=call
    )
    # Not in §4.2b: which profile was used (body | draft | active | example).
    return {**out, "profile_source": where}


@router.post("/simulate")
async def simulate(body: PreviewBody, request: Request):
    api: Api = request.app.state.api
    profile, where = await _profile_for(api, body.profile)
    call_id, record, call, stored = await _source(api, body.source)
    transcript = None
    uc = exporter.use_case_for(profile, record, call)
    if stored and uc is not None and uc.transcript.enabled:
        transcript = ((await api.store.get(TRANSCRIPTS, call_id)) or {}).get("turns")
    out = await exporter.simulate(
        profile, record, call_id=call_id, call=call, transcript=transcript, now=api.now()
    )
    return {**out, "profile_source": where}


# --------------------------------------------------------------------------- outbox


@router.get("/outbox")
async def outbox(request: Request, status: str | None = Query(None, max_length=32)):
    api: Api = request.app.state.api
    if status is not None and status not in OUTBOX_STATUSES:
        raise HTTPException(422, f"status is one of {sorted(OUTBOX_STATUSES)}.")
    where = [Where("status", "==", status)] if status else None
    rows = await api.store.query(OUTBOX, where, order_by="created_at", descending=True, limit=200)
    return {"records": [_outbox_row(cid, rec) for cid, rec in rows]}


@router.post("/outbox/{call_id}/requeue")
async def requeue(call_id: str, request: Request):
    api: Api = request.app.state.api
    if not CALL_ID_RE.match(call_id):
        raise HTTPException(400, "invalid call id")
    seen: dict[str, Any] = {}

    def fn(cur: dict | None) -> dict | None:
        seen["status"] = (cur or {}).get("status") if cur else None
        seen["exists"] = cur is not None
        if cur is None or cur.get("status") in NOT_REQUEUEABLE:
            return None
        new = {k: v for k, v in cur.items() if k != "export_lease_until"}
        new.update(status=exporter.PENDING, last_error=None, export_attempts=0)
        return new

    doc = await api.store.transact(OUTBOX, call_id, fn)
    if not seen.get("exists"):
        raise HTTPException(404, "No outbox record for that call.")
    if seen.get("status") in NOT_REQUEUEABLE:
        raise HTTPException(409, "A withdrawn record cannot be requeued.")
    return _outbox_row(call_id, doc or {})
