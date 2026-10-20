"""Firestore + GCS implementation of :class:`CallSink`.

Collections (shared with the API):
  pending_calls/{call_id}  per-call patient context incl. DOB; read once, then deleted
  calls/{call_id}          lifecycle doc (never holds the DOB)
  reports/{report_id}      validated call report
  transcripts/{call_id}    {"turns": [...]}
  call_events/{call_id}    {"timeline": [...]} for latency debugging
Recordings: gs://<recordings bucket>/recordings/<call_id>.wav
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from voa_core import lifecycle, reports
from voa_core.store import DocStore, now_iso

log = logging.getLogger(__name__)

PENDING, CALLS, REPORTS, TRANSCRIPTS, EVENTS = (
    "pending_calls",
    "calls",
    "reports",
    "transcripts",
    "call_events",
)


class StoreCallSink:
    def __init__(
        self,
        store: DocStore,
        *,
        recordings_bucket: str | None,
        project_id: str,
        call_id_pattern: re.Pattern[str],
    ):
        self.store = store
        self.bucket = recordings_bucket
        self.project_id = project_id
        self.call_id_pattern = call_id_pattern
        self._patients: dict[str, dict[str, Any]] = {}

    async def load_patient(self, call_id: str, wait_s: float = 3.0) -> dict[str, Any] | None:
        # The dialler writes pending_calls right after the provider returns the call id,
        # so on a fast answer it can land a moment after the media stream starts.
        deadline = asyncio.get_running_loop().time() + wait_s
        while True:
            doc = await self.store.get(PENDING, call_id)
            if doc or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.25)
        if doc:
            await self.store.delete(PENDING, call_id)  # read-once: PHI does not accumulate
            self._patients[call_id] = doc
        return doc

    def remember_patient(self, call_id: str, patient: dict[str, Any]) -> None:
        self._patients[call_id] = patient

    async def call_doc(self, call_id: str) -> dict[str, Any] | None:
        return await self.store.get(CALLS, call_id)

    def watch_call(self, call_id: str):
        return self.store.watch(CALLS, call_id)

    async def event(self, call_id: str, kind: str, **fields: Any) -> None:
        now = datetime.now(UTC)

        def mutate(cur):
            patch = lifecycle.call_event_patch(kind, now=now, current=cur, **fields)
            return lifecycle.merge_call_doc(cur, patch, now=now)

        await self.store.transact(CALLS, call_id, mutate)

    async def mark_amd_released(self, call_id: str) -> None:
        """The agent was bridged to an ambiguous machine verdict: a later 'completed'
        must not be classified as left_vmail just because AMD said machine."""
        now = datetime.now(UTC)
        await self.store.transact(
            CALLS,
            call_id,
            lambda cur: lifecycle.merge_call_doc(cur, {"amd_released": True}, now=now),
        )

    async def save_report(self, call_id: str, report: dict[str, Any]) -> None:
        patient = self._patients.get(call_id, {})
        payload = {
            "patient_id": patient.get("patient_id") or "unknown",
            "call_sid": call_id,
            "channel": "voice",
            **report,
        }
        clean, errors = reports.validate_report(payload, call_id_pattern=self.call_id_pattern)
        if errors:
            # Keep the raw report rather than lose it; flag it for a human.
            log.error(
                "report failed validation", extra={"event": "report.invalid", "errors": errors}
            )
            await self.store.merge(
                CALLS, call_id, {"report_errors": errors, "updated_at": now_iso()}
            )
            return
        now = datetime.now(UTC)
        report_id = reports.report_doc_id(clean["patient_id"], now)
        await self.store.set(
            REPORTS, report_id, {**clean, "call_id": call_id, "created_at": now_iso(now)}
        )
        patch, _settles = reports.report_call_patch(clean, report_id)
        await self.store.transact(
            CALLS,
            call_id,
            lambda cur: lifecycle.merge_call_doc(cur, patch, now=now, from_report=True),
        )
        log.info("report saved", extra={"event": "report.saved", "outcome": clean.get("outcome")})

    async def finish(self, call_id, *, transcript, timeline, recording_wav) -> None:
        self._patients.pop(call_id, None)
        # A call deleted while it was ending (a nurse's bulk delete, a smoke test's
        # cleanup) must stay deleted: writing the transcript or the recording link
        # here used to recreate an empty call record with no patient.
        if await self.store.get(CALLS, call_id) is None:
            log.info(
                "call deleted before it finished; nothing saved",
                extra={"event": "call.finish_skipped"},
            )
            return
        now = now_iso()
        await self.store.set(TRANSCRIPTS, call_id, {"turns": transcript, "created_at": now})
        await self.store.set(EVENTS, call_id, {"timeline": timeline, "created_at": now})
        if recording_wav and self.bucket:
            uri = await asyncio.to_thread(self._upload, call_id, recording_wav)
            await self.store.transact(
                CALLS,
                call_id,
                # Never create the doc: only add the link to a call that still exists.
                lambda cur: (
                    None
                    if cur is None
                    else lifecycle.merge_call_doc(
                        cur, {"recording_uri": uri}, now=datetime.now(UTC)
                    )
                ),
            )

    def _upload(self, call_id: str, wav: bytes) -> str:
        from google.cloud import storage

        name = f"recordings/{call_id}.wav"
        storage.Client(project=self.project_id).bucket(self.bucket).blob(name).upload_from_string(
            wav, content_type="audio/wav"
        )
        return f"gs://{self.bucket}/{name}"
