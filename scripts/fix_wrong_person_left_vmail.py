#!/usr/bin/env python3
"""Correct calls left at ``left_vmail`` with a person-answered outcome (22 Oct 2026).

Before the 22 Oct fix a call could end as ``call_status: left_vmail`` with a
non-reaching outcome (wrong person, identity failed, consent declined). Two ways:

* ``drop_overwritten``: the gateway dropped the voicemail clip and filed
  ``voicemail_left``; the model, still running behind the muted line, then filed
  its own report from the greeting and overwrote the outcome. The machine was
  real, so ``left_vmail`` is RIGHT and the OUTCOME is wrong: it is put back to the
  drop's ``voicemail_left`` report (outcome, follow-up fields, report_doc_id).
* ``inferred_voicemail``: no drop report; ``left_vmail`` came from an AMD machine
  verdict while the agent spoke to a person. The STATUS is wrong: it becomes
  ``interrupted``, what ``lifecycle.classify_completed`` now gives these facts.

Left alone: an outcome filed by a by-text questionnaire (``channel: sms``), whose
voicemail was real. The ``sms_follow_up`` stamp is history and is never touched,
nor are report docs. Every write adds a ``correction`` note to the call.

Dry run by default; ``--apply`` writes. Prints call ids by their last 4 only.

  uv run python scripts/fix_wrong_person_left_vmail.py <project-id> [--apply]
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from voa_core import lifecycle

#: Call-doc fields restored from the drop's report.
RESTORED_FIELDS = (
    "outcome",
    "follow_up",
    "follow_up_reason",
    "follow_up_reasons",
    "dob_verification_result",
)


def plan(
    call: Mapping[str, Any], reports: Iterable[tuple[str, Mapping[str, Any]]]
) -> tuple[str, dict[str, Any]]:
    """(kind, patch) for one call doc and its reports. An empty patch: leave it."""
    outcome = call.get("outcome")
    if call.get("call_status") != lifecycle.LEFT_VMAIL:
        return "not_left_vmail", {}
    if outcome not in lifecycle.NON_REACHING_OUTCOMES:
        return "ok", {}
    if call.get("channel") == "sms":
        return "sms_outcome", {}
    drops = sorted(
        (
            (rid, r)
            for rid, r in reports
            if r.get("outcome") == "voicemail_left" and r.get("source") != "sms"
        ),
        key=lambda x: str(x[1].get("created_at") or ""),
    )
    if drops:
        rid, rep = drops[0]
        patch = {k: rep[k] for k in RESTORED_FIELDS if k in rep}
        patch["report_doc_id"] = rid
        return "drop_overwritten", patch
    return "inferred_voicemail", {"call_status": lifecycle.INTERRUPTED}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_id")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    from google.cloud import firestore

    db = firestore.Client(project=a.project_id)
    counts: Counter[str] = Counter()
    now = datetime.now(UTC).isoformat()
    query = db.collection("calls").where(
        filter=firestore.FieldFilter("call_status", "==", lifecycle.LEFT_VMAIL)
    )
    for snap in query.stream():
        call = snap.to_dict() or {}
        reps = [
            (r.id, r.to_dict() or {})
            for r in db.collection("reports")
            .where(filter=firestore.FieldFilter("call_id", "==", snap.id))
            .stream()
        ]
        kind, patch = plan(call, reps)
        counts[kind] += 1
        if not patch:
            continue
        shown = {k: v for k, v in patch.items() if k in ("call_status", "outcome")}
        print(
            f"{'fix' if a.apply else 'would fix'} call …{snap.id[-4:]} ({kind}): "
            f"status {call.get('call_status')} outcome {call.get('outcome')} -> {shown}"
        )
        if a.apply:
            note = {
                "at": now,
                "by": "scripts/fix_wrong_person_left_vmail.py",
                "kind": kind,
                "was": {"call_status": call.get("call_status"), "outcome": call.get("outcome")},
            }
            snap.reference.update({**patch, "correction": note, "updated_at": now})
    print(dict(counts), "(applied)" if a.apply else "(dry run: nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
