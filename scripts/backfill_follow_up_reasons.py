#!/usr/bin/env python3
"""Give calls filed before 19 Oct 2026 their full ``follow_up_reasons`` list.

Re-runs today's triage on each completed call's stored answers. It only fills in
the reasons (and the reason sentence) when:

* the call has answers and no ``follow_up_reasons`` yet,
* no nurse has changed its follow-up (``follow_up_override`` absent), and
* the recomputed tier equals the stored ``follow_up`` -- a backfill never
  changes how urgent a call is; a call whose rules changed is listed and left.

Dry run by default; ``--apply`` writes.

  uv run python scripts/backfill_follow_up_reasons.py <project-id> [--apply]
"""

from __future__ import annotations

import argparse

from google.cloud import firestore
from voa_core import survey


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_id")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    db = firestore.Client(project=a.project_id)
    filled = skipped = 0
    for snap in db.collection("calls").stream():
        d = snap.to_dict() or {}
        answers = d.get("survey_responses")
        if not answers or d.get("follow_up_reasons") or d.get("follow_up_override"):
            continue
        if d.get("outcome") != "questionnaire_completed":
            continue
        t = survey.triage(answers)
        if t.follow_up != d.get("follow_up"):
            skipped += 1
            print(f"skip {snap.id[:10]}…: stored {d.get('follow_up')} vs rules now {t.follow_up}")
            continue
        filled += 1
        print(f"{'fill' if a.apply else 'would fill'} {snap.id[:10]}…: {len(t.reasons)} reasons")
        if a.apply:
            snap.reference.update({"follow_up_reasons": t.reasons, "follow_up_reason": t.reason})
    print(f"{filled} {'filled' if a.apply else 'to fill'}, {skipped} skipped (tier differs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
