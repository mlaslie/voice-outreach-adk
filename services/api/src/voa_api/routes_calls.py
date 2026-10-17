"""/api/calls*, /api/place-call, /api/test-call."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from voa_core import reports
from voa_core.config import resolve_secret
from voa_core.store import Where, now_iso

from .auth import acting_user, require_caller, ui_callers
from .context import CALL_ID_RE, CALLS, EVENTS, PENDING, REPORTS, TRANSCRIPTS, Api
from .dialer import DialError, dial
from .models import BatchDelete, FollowUpOverride, PlaceCallRequest, TestCallRequest


def api_of(request: Request) -> Api:
    require_caller(request, ui_callers(request))
    return request.app.state.api


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
            "timestamp": (t0 + timedelta(milliseconds=x.get("at_ms", 0))).isoformat() if t0 else "",
        }
        for x in t["turns"]
    ]
    return {"turns": turns, "available": True, "conversation_id": call_id}


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
            yield blob.download_as_bytes(start=pos, end=stop + 1)
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

    def mutate(doc):
        if doc is None:
            return None
        event = {
            "at": now,
            "from": doc.get("follow_up"),
            "to": body.follow_up,
            "reason": body.reason,
            "set_by": by,
        }
        new = dict(doc)
        if "agent_follow_up" not in new:
            new["agent_follow_up"] = doc.get("follow_up")  # the agent's own tier is kept
        new["follow_up"] = body.follow_up
        new["follow_up_override"] = event
        new["follow_up_history"] = [*doc.get("follow_up_history", []), event]
        new["updated_at"] = now
        return new

    doc = await api.store.transact(CALLS, call_id, mutate)
    if not doc:
        raise HTTPException(404, "Call not found.")
    return _pub(call_id, doc)


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


@router.post("/test-call")
async def test_call(body: TestCallRequest, request: Request):
    """Mint a browser softphone session: stage a patient (a listed entry, or the
    fictional test patient) and return a short-lived signed URL to the gateway."""
    api: Api = request.app.state.api
    if not api.cfg.softphone.enabled or not api.cfg.softphone.token_secret:
        raise HTTPException(404, "Test calls are disabled.")
    key = resolve_secret(api.cfg.softphone.token_secret, api.cfg.gcp.project_id)
    call_id = "browser_" + uuid.uuid4().hex[:16]
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
    else:
        patient = {
            "first_name": "Jordan",
            "last_name": "Rivera",
            "patient_name": "Jordan Rivera",
            "patient_id": "TEST-0001",
            "dob": "1950-03-15",
            "is_diabetic": False,
        }
    await api.store.set(PENDING, call_id, patient)
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
        },
    )
    exp = str(int(time.time()) + 300)
    sig = hmac.new(key.encode(), f"{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()
    url = f"{api.cfg.services.gateway_url.rstrip('/')}/softphone?token={call_id}.{exp}.{sig}"
    return {"call_sid": call_id, "softphone_url": url, "expires_in": 300}
