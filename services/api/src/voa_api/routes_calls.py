"""/api/calls*, /api/follow-ups/open, /api/place-call, /api/test-call.

Review and sign-off (rules in ``voa_core.review``): a completed assessment is
final only once a nurse signs it off; signing off a follow-up resolves it and
needs the outcome of the nurse's callback as a note; each sign-off writes the
structured result to ``ehr_outbox/{call_id}`` (``pending_export``) for a separate
EHR integration; a later tier change or report reopens it and withdraws that
record.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from voa_core import reports, review
from voa_core.config import resolve_secret
from voa_core.store import Where, now_iso

from .auth import acting_user, authorize
from .context import CALL_ID_RE, CALLS, EHR_OUTBOX, EVENTS, PENDING, REPORTS, TRANSCRIPTS, Api
from .dialer import DialError, dial
from .models import (
    BatchDelete,
    BulkSignOff,
    FollowUpOverride,
    PlaceCallRequest,
    SignOff,
    TestCallRequest,
)

log = logging.getLogger(__name__)


async def api_of(request: Request):
    """Per-route roles (auth.ROUTE_POLICY), then, after a successful configuration
    change, one audit line (audit.py)."""
    authorize(request)
    api: Api = request.app.state.api
    yield api
    from . import audit

    await audit.record(api, request)


router = APIRouter(prefix="/api", dependencies=[Depends(api_of)])


def _pub(doc_id: str, doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("patient_dob", None)
    if isinstance(doc.get("patient_context"), dict):
        doc["patient_context"] = {
            k: v for k, v in doc["patient_context"].items() if k not in ("dob", "patient_dob")
        }
    return {"doc_id": doc_id, **doc}


def _check_id(call_id: str) -> None:
    if not CALL_ID_RE.match(call_id):
        raise HTTPException(400, "invalid call id")


@router.get("/calls")
async def list_calls(request: Request, limit: int = 100, status: str | None = None):
    api: Api = request.app.state.api
    limit = max(1, min(limit, 500))
    where = [Where("call_status", "==", status)] if status else None
    rows = await api.store.query(
        CALLS, where, order_by="initiated_at", descending=True, limit=limit
    )
    calls = [_pub(i, d) for i, d in rows]
    return {"calls": calls, "count": len(calls)}


@router.get("/follow-ups/open")
async def open_follow_ups(request: Request, limit: int | None = None):
    """Every report whose callback is still open, from the WHOLE collection.

    /api/calls returns only the newest calls, so an older open follow-up fell out
    of every list. A report that needed a callback stays open until a nurse closes
    it, whatever came after it (D-1), so "open" is simply: the tier needs a
    callback and the review is not closed. No cap by default: open reports only
    leave when a nurse closes them, so a cap would silently hide some. Callers
    group the rows by patient."""
    api: Api = request.app.state.api
    rows = await api.store.query(CALLS, [Where("follow_up", "in", list(review.NEEDS_TIERS))])
    out = review.sort_open_follow_ups(_pub(i, d) for i, d in rows if review.is_open_follow_up(d))
    return {"calls": out[: max(1, limit)] if limit else out, "count": len(out)}


@router.get("/calls/stream")
async def stream_calls(request: Request):
    """SSE: ``init`` with recent calls, then ``call`` for each changed doc.
    Polls ``updated_at`` (single-field index) every 2 s; one poll per client."""
    api: Api = request.app.state.api

    async def events():
        rows = await api.store.query(CALLS, order_by="initiated_at", descending=True, limit=100)
        watermark = max((d.get("updated_at", "") for _, d in rows), default=now_iso(api.now()))
        yield f"event: init\ndata: {json.dumps({'calls': [_pub(i, d) for i, d in rows]})}\n\n"
        last_ping = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(2.0)
            changed = await api.store.query(
                CALLS, [Where("updated_at", ">", watermark)], order_by="updated_at", limit=200
            )
            for i, d in changed:
                watermark = max(watermark, d.get("updated_at", ""))
                yield f"event: call\ndata: {json.dumps(_pub(i, d))}\n\n"
            if time.monotonic() - last_ping > 15:
                last_ping = time.monotonic()
                yield ": ping\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/calls/{call_id}")
async def get_call(call_id: str, request: Request):
    _check_id(call_id)
    doc = await request.app.state.api.store.get(CALLS, call_id)
    if not doc:
        raise HTTPException(404, "Call not found.")
    return _pub(call_id, doc)


@router.get("/calls/{call_id}/transcript")
async def transcript(call_id: str, request: Request):
    _check_id(call_id)
    api: Api = request.app.state.api
    t = await api.store.get(TRANSCRIPTS, call_id)
    if not t or not t.get("turns"):
        return {"turns": [], "available": False, "reason": "No transcript recorded for this call."}
    call = await api.store.get(CALLS, call_id) or {}
    base = call.get("answered_at") or call.get("initiated_at")
    t0 = datetime.fromisoformat(base) if base else None
    turns = [
        {
            "role": "agent" if x.get("role") == "agent" else "patient",
            "text": x.get("text", ""),
            # Bilingual transcripts (docs/LANGUAGES-PLAN.md step 2): set by the
            # translation task; null before it ran, and text_en is null for a
            # turn already in English.
            "text_en": x.get("text_en"),
            "lang": x.get("lang"),
            "timestamp": (t0 + timedelta(milliseconds=x.get("at_ms", 0))).isoformat() if t0 else "",
        }
        for x in t["turns"]
    ]
    return {
        "turns": turns,
        "available": True,
        "conversation_id": call_id,
        # null: not run yet (pending); else {status: done|failed|skipped, model, at, languages}
        "translation": t.get("translation"),
        "language": call.get("language") or (call.get("patient_context") or {}).get("language"),
        "language_used": call.get("language_used"),
        # approved | draft_pack | machine (demo auto-approve); absent for English.
        "language_source": call.get("language_source"),
        # The use case's display title, for the download's title line.
        "title": _use_case_title(call.get("campaign_id")),
    }


def _use_case_title(campaign_id: str | None) -> str:
    from voa_core.campaign import DEFAULT_CAMPAIGN_ID, load_campaign

    try:
        c = load_campaign(campaign_id or DEFAULT_CAMPAIGN_ID)
    except (OSError, ValueError):
        return ""
    return c.display.title or c.name


@router.get("/calls/{call_id}/recording")
async def recording(call_id: str, request: Request):
    """Stream the WAV from GCS with the API's credentials (browser never gets a GCS URL).
    Honors a single ``Range`` header so the audio element can seek (review P-7)."""
    _check_id(call_id)
    api: Api = request.app.state.api
    call = await api.store.get(CALLS, call_id)
    uri = (call or {}).get("recording_uri", "")
    if not uri.startswith("gs://"):
        raise HTTPException(404, "No recording for this call.")
    bucket, _, name = uri[5:].partition("/")
    from google.cloud import storage

    blob = await asyncio.to_thread(
        lambda: storage.Client(project=api.cfg.gcp.project_id).bucket(bucket).get_blob(name)
    )
    if blob is None:
        raise HTTPException(404, "Recording missing.")
    size = blob.size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range", "")
    if rng.startswith("bytes="):
        a, _, b = rng[6:].partition("-")
        start = int(a) if a else max(0, size - int(b or 0))
        end = min(int(b), size - 1) if a and b else end
        status = 206

    def chunks():
        pos, step = start, 256 * 1024
        while pos <= end:
            stop = min(pos + step - 1, end)
            # GCS ``end`` is inclusive (the last byte). end=stop+1 sent one extra byte per
            # chunk, overran Content-Length and every playback failed with a 502.
            yield blob.download_as_bytes(start=pos, end=stop)
            pos = stop + 1

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type="audio/wav", headers=headers)


@router.post("/calls/{call_id}/follow-up")
async def follow_up_override(call_id: str, body: FollowUpOverride, request: Request):
    _check_id(call_id)
    if body.follow_up not in reports.FOLLOW_UP_TIERS:
        raise HTTPException(400, f"follow_up must be one of {', '.join(reports.FOLLOW_UP_TIERS)}")
    api: Api = request.app.state.api
    by = acting_user(request)
    now = now_iso(api.now())

    withdraw: dict[str, bool] = {}

    def mutate(doc):
        withdraw.clear()
        if doc is None:
            return None
        note = body.reason or "; ".join(body.reasons or [])
        event = {
            "at": now,
            "from": doc.get("follow_up"),
            "to": body.follow_up,
            "reason": note,
            "set_by": by,
        }
        new = dict(doc)
        if "agent_follow_up" not in new:
            new["agent_follow_up"] = doc.get("follow_up")  # the agent's own tier is kept
        if body.reasons:
            event["reasons"] = list(body.reasons)
            if "agent_follow_up_reasons" not in new:
                # The agent's own findings and sentence are kept for the record.
                new["agent_follow_up_reasons"] = doc.get("follow_up_reasons") or []
                new["agent_follow_up_reason"] = doc.get("follow_up_reason")
            new["follow_up_reasons"] = [
                {"tier": body.follow_up, "text": r, "source": "nurse", "set_by": by, "at": now}
                for r in body.reasons
            ]
            new["follow_up_reason"] = "Nurse: " + "; ".join(body.reasons) + "."
        new["follow_up"] = body.follow_up
        new["follow_up_override"] = event
        new["follow_up_history"] = [*doc.get("follow_up_history", []), event]
        new["updated_at"] = now
        # A changed tier invalidates a sign-off made on the old one; a superseded
        # report re-tiered by a nurse comes back into the lists (B-5).
        reopen, withdraw["export"] = review.reopen_after_tier_change(doc, now=now, by=by)
        new.update(reopen)
        return new

    doc = await api.store.transact(CALLS, call_id, mutate)
    if not doc:
        raise HTTPException(404, "Call not found.")
    if withdraw.get("export"):
        await api.store.merge(EHR_OUTBOX, call_id, review.withdrawn_export(now))
    return _pub(call_id, doc)


# --------------------------------------------------------------------------- sign-off


def _signer(request: Request, signed_by: str | None) -> str:
    """A sign-off is a clinical signature: refused without a verified signer
    rather than filed as "unverified" (B-7). The signer is the signed-in user the
    BFF (or the nurse assistant) forwards; the body's ``signed_by`` counts only in
    dev/test, where nothing in front of the API sets the header."""
    api: Api = request.app.state.api
    who = review.verified_signer(request.headers.get("x-acting-user"))
    if who is None and api.cfg.env in ("dev", "test"):
        who = review.verified_signer(signed_by)
    if who is None:
        raise HTTPException(
            422, "A sign-off needs the signer: who is signing could not be determined."
        )
    return who


async def _sign_off_one(
    api: Api,
    call_id: str,
    *,
    signer: str,
    note: str | None,
    expected_follow_up: str | None = None,
    bulk: bool = False,
) -> review.SignOffPlan:
    """Plan the sign-off from the call and the patient's other calls, then write
    it. The call is written only if it is unchanged since it was read (a report
    or tier change landing meanwhile makes this "changed"); each earlier report is
    closed only if it is still open."""
    doc = await api.store.get(CALLS, call_id)
    others: list[tuple[str, dict]] = []
    if doc and (f := review.patient_filter(doc)):
        others = await api.store.query(CALLS, [Where(f[0], "==", f[1])])
    now = now_iso(api.now())
    plan = review.plan_sign_off(
        call_id,
        doc,
        others,
        signer=signer,
        now=now,
        note=note,
        expected_follow_up=expected_follow_up,
        bulk=bulk,
    )
    if plan.status != "signed_off" or doc is None:
        return plan
    signed = plan.review or {}

    def sign(cur):
        if (
            cur is None
            or review.review_closed(cur)
            or cur.get("follow_up") != doc.get("follow_up")
            or cur.get("updated_at") != doc.get("updated_at")
        ):
            return None
        return {**cur, **review.close_patch(cur, signed, now)}

    written = await api.store.transact(CALLS, call_id, sign)
    if (written or {}).get("review") != signed:
        return review.SignOffPlan("changed", follow_up=(written or {}).get("follow_up"))
    for sid, closed in plan.closes.items():

        def close(cur, closed=closed):
            if cur is None or review.review_closed(cur):
                return None
            return {**cur, **review.close_patch(cur, closed, now)}

        await api.store.transact(CALLS, sid, close)
    for sid, record in plan.exports.items():
        await api.store.set(EHR_OUTBOX, sid, record)
    return plan


@router.post("/calls/sign-off")
async def bulk_sign_off(body: BulkSignOff, request: Request):
    """Sign off several CLEARED calls at once. Anything that resolves a follow-up
    is refused here (needs_individual_review): it must be resolved one by one."""
    api: Api = request.app.state.api
    signer = _signer(request, body.signed_by)
    results = []
    for sid in dict.fromkeys(body.call_sids):
        if not CALL_ID_RE.match(sid):
            results.append({"call_sid": sid, "status": "invalid_sid"})
            continue
        plan = await _sign_off_one(api, sid, signer=signer, note=body.note, bulk=True)
        results.append({"call_sid": sid, "status": plan.status, "superseded": plan.superseded})
    done = sum(r["status"] == "signed_off" for r in results)
    return {"results": results, "signed_off": done}


@router.post("/calls/{call_id}/sign-off")
async def sign_off_call(call_id: str, body: SignOff, request: Request):
    """Sign off one call. A call that needs follow-up (or whose patient has an
    earlier open report that does) is resolved by this and needs the outcome of
    the nurse's callback as ``note``."""
    _check_id(call_id)
    api: Api = request.app.state.api
    signer = _signer(request, body.signed_by)
    plan = await _sign_off_one(
        api,
        call_id,
        signer=signer,
        note=body.note,
        expected_follow_up=body.expected_follow_up,
    )
    st = plan.status
    if st == "not_found":
        raise HTTPException(404, "Call not found.")
    if st == "not_an_assessment":
        raise HTTPException(409, "Nothing to sign off: this call has no assessment.")
    if st == "already_signed_off":
        raise HTTPException(409, "Already signed off.")
    if st == "superseded_by_later":
        raise HTTPException(
            409,
            f"A later report for this patient ({plan.latest}) replaces this one; sign off "
            "that one, which closes this one too.",
        )
    if st == "changed":
        raise HTTPException(
            409, f"The follow-up changed to {plan.follow_up} since you looked; review it again."
        )
    if st == "note_required":
        raise HTTPException(
            422,
            "Resolving a follow-up needs a note: what happened when you called the patient."
            + (
                " This patient also has an earlier report needing a callback; the note "
                "resolves it too."
                if plan.earlier_needs
                else ""
            ),
        )
    return {
        "call_sid": call_id,
        "review": plan.review,
        "superseded": plan.superseded,
        "also_resolved": plan.earlier_needs,
    }


@router.post("/calls/batch-delete")
async def batch_delete(body: BatchDelete, request: Request):
    api: Api = request.app.state.api
    results = []
    for call_id in body.call_sids:
        if not CALL_ID_RE.match(call_id):
            results.append({"call_sid": call_id, "status": "invalid_sid"})
            continue
        try:
            call = await api.store.get(CALLS, call_id)
            if call is None:
                results.append({"call_sid": call_id, "status": "not_found"})
                continue
            removed = {
                "calls_doc": False,
                "call_records_doc": False,
                "post_op_reports_doc": False,
                "gcs_objects": 0,
            }
            if rid := call.get("report_doc_id"):
                removed["post_op_reports_doc"] = await api.store.delete(REPORTS, rid)
            for coll in (TRANSCRIPTS, EVENTS, PENDING):
                await api.store.delete(coll, call_id)
            removed["call_records_doc"] = True
            uri = call.get("recording_uri", "")
            if uri.startswith("gs://"):
                removed["gcs_objects"] = await asyncio.to_thread(_delete_blob, api, uri)
            removed["calls_doc"] = await api.store.delete(CALLS, call_id)
            results.append({"call_sid": call_id, "status": "deleted", "removed": removed})
        except Exception:
            results.append(
                {"call_sid": call_id, "status": "error", "error": "delete failed"}
            )  # no str(e) (P-13)
    # Audit line: counts and the acting nurse only, never a patient name or number.
    log.info(
        "calls batch deleted",
        extra={
            "event": "calls.batch_deleted",
            "requested": len(body.call_sids),
            "count": sum(r["status"] == "deleted" for r in results),
            "failed": sum(r["status"] == "error" for r in results),
            "by": acting_user(request),
        },
    )
    return {"results": results}


def _delete_blob(api: Api, uri: str) -> int:
    from google.cloud import storage

    bucket, _, name = uri[5:].partition("/")
    blob = storage.Client(project=api.cfg.gcp.project_id).bucket(bucket).blob(name)
    if blob.exists():
        blob.delete()
        return 1
    return 0


@router.post("/place-call")
async def place_call(body: PlaceCallRequest, request: Request):
    api: Api = request.app.state.api
    try:
        call_id = await dial(api, body, placed_via="dashboard")
    except DialError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    return {"call_sid": call_id, "status": "queued"}


@router.get("/calls/{call_id}/events")
async def call_events(call_id: str, request: Request):
    """The gateway's millisecond timeline for one call (admin diagnostics). Events
    carry names and statuses only: tool names and results, never patient answers."""
    _check_id(call_id)
    api: Api = request.app.state.api
    doc = await api.store.get(EVENTS, call_id) or {}
    tl = list(doc.get("timeline") or [])

    def first(name: str):
        return next((e.get("ms") for e in tl if e.get("event") == name), None)

    start, audio = first("media.start"), first("agent.audio_start")
    return {
        "call_id": call_id,
        "available": bool(tl),
        "timeline": tl,
        "summary": {
            "events": len(tl),
            "first_audio_ms": (audio - start) if audio is not None and start is not None else None,
            "tool_calls": sum(1 for e in tl if e.get("event") == "tool.call"),
            "interruptions": sum(1 for e in tl if e.get("event") == "agent.interrupted"),
            "errors": sum(1 for e in tl if str(e.get("event", "")).startswith("live.")),
        },
    }


@router.get("/audit")
async def audit_log(request: Request, limit: int = 200):
    """Configuration changes, newest first (audit.py)."""
    from . import audit

    api: Api = request.app.state.api
    rows = await api.store.query(audit.COLLECTION)
    rows.sort(key=lambda r: r[1].get("at", ""), reverse=True)
    return {"entries": [{"id": i, **d} for i, d in rows[: max(1, min(limit, 1000))]]}


@router.post("/calls/{call_id}/recording-link")
async def recording_link(call_id: str, request: Request):
    """A 15-minute link to this call's recording on the public gateway, so the nurse
    agent can play it inside its Gemini Enterprise card (the browser there cannot
    reach this private API). Signed with the softphone key, domain-separated."""
    from voa_core import links

    _check_id(call_id)
    api: Api = request.app.state.api
    call = await api.store.get(CALLS, call_id)
    if not str((call or {}).get("recording_uri", "")).startswith("gs://"):
        raise HTTPException(404, "No recording for this call.")
    if not api.cfg.softphone.token_secret or not api.cfg.services.gateway_url:
        raise HTTPException(503, "Recording links are not configured.")
    key = resolve_secret(api.cfg.softphone.token_secret, api.cfg.gcp.project_id)
    url, exp = links.recording_link(api.cfg.services.gateway_url, key, call_id)
    log.info(
        "recording link minted",
        extra={"event": "recording.link_minted", "call_id": call_id, "by": acting_user(request)},
    )
    return {
        "url": url,
        "expires_at": datetime.fromtimestamp(exp, UTC).isoformat(),
        "expires_in": links.RECORDING_LINK_TTL_S,
    }


@router.post("/test-call")
async def test_call(body: TestCallRequest, request: Request):
    """Mint a browser softphone session: stage a patient (a listed entry, or the
    fictional test patient) and return a short-lived signed URL to the gateway."""
    api: Api = request.app.state.api
    if not api.cfg.softphone.enabled or not api.cfg.softphone.token_secret:
        raise HTTPException(404, "Test calls are disabled.")
    key = resolve_secret(api.cfg.softphone.token_secret, api.cfg.gcp.project_id)
    call_id = "browser_" + uuid.uuid4().hex[:16]
    campaign_id = body.campaign_id
    if body.entry_id:
        from .routes_list import entry_or_404

        e = await entry_or_404(api, body.entry_id)
        first, _, last = e.get("patient_name", "").partition(" ")
        patient = {
            "first_name": first,
            "last_name": last,
            "patient_name": e.get("patient_name"),
            "patient_id": e.get("patient_id"),
            "dob": e.get("patient_dob"),
            "is_diabetic": e.get("is_diabetic", False),
        }
        campaign_id = campaign_id or e.get("campaign_id")
    else:
        patient = {
            "first_name": "Jordan",
            "last_name": "Rivera",
            "patient_name": "Jordan Rivera",
            "patient_id": "TEST-0001",
            "dob": "1950-03-15",
            "is_diabetic": False,
        }
    from voa_core.campaign import DEFAULT_CAMPAIGN_ID, load_campaign

    campaign_id = campaign_id or DEFAULT_CAMPAIGN_ID
    try:
        spec_version = load_campaign(campaign_id).version
    except (OSError, ValueError):
        raise HTTPException(422, f"Unknown use case {campaign_id!r}.") from None
    use_case = {"campaign_id": campaign_id, "spec_version": spec_version}
    await api.store.set(PENDING, call_id, {**patient, **use_case})
    now = now_iso(api.now())
    await api.store.set(
        CALLS,
        call_id,
        {
            "call_sid": call_id,
            "to": "browser",
            "from": "browser",
            "initiated_at": now,
            "updated_at": now,
            "patient_id": patient["patient_id"],
            "patient_name": patient["patient_name"],
            "placed_via": "test_call",
            "amd_requested": False,
            "channel": "voice",
            **use_case,
        },
    )
    exp = str(int(time.time()) + 300)
    sig = hmac.new(key.encode(), f"{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    url = f"{api.cfg.services.gateway_url.rstrip('/')}/softphone?token={call_id}.{exp}.{sig}"
    return {"call_sid": call_id, "softphone_url": url, "expires_in": 300}
