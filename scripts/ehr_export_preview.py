#!/usr/bin/env python3
"""Print the FHIR Bundle an EHR export profile would send for one outbox record.

Offline: no FHIR server is contacted; the patient reference is a placeholder.

  uv run python scripts/ehr_export_preview.py config/ehr/profiles/post_op.example.yaml \
      config/ehr/samples/post_op.outbox.json [--patient-id example-patient] [--check-only]

The fixture is either a bare ehr_outbox record, or {"call_id", "record", "call"}
(the call doc supplies the use case and spec version). Exit status 1 when the
profile or the bundle has errors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from voa_ehr.exporter import preview
from voa_ehr.profile import ProfileError, profile_from_text, profile_problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("profile", type=Path)
    ap.add_argument("fixture", type=Path, nargs="?")
    ap.add_argument("--patient-id", default="example-patient")
    ap.add_argument("--check-only", action="store_true", help="check the profile only")
    a = ap.parse_args(argv)
    try:
        profile = profile_from_text(a.profile.read_text())
    except ProfileError as e:
        for p in e.problems:
            print(f"error   {p.path}: {p.message}", file=sys.stderr)
        return 1
    problems = profile_problems(profile)
    for p in problems:
        print(f"{p.level:7} {p.path}: {p.message}", file=sys.stderr)
    if any(p.is_error for p in problems):
        return 1
    if a.check_only or a.fixture is None:
        print("profile OK", file=sys.stderr)
        return 0
    data = json.loads(a.fixture.read_text())
    if "record" in data:
        record, call, call_id = data["record"], data.get("call"), data.get("call_id")
    else:
        record, call, call_id = data, None, None
    call_id = call_id or record.get("call_sid") or "example-call"
    result = asyncio.run(
        preview(None, profile, record, call_id=call_id, call=call, patient_id=a.patient_id)
    )
    for s in result.get("skipped", []):
        print(f"skipped {s['question_id']}: {s['reason']} ({s['source']})", file=sys.stderr)
    for w in result.get("warnings", []):
        print(f"warning {w}", file=sys.stderr)
    for err in result.get("errors", []):
        print(f"error   {err.get('path') or ''} {err['message']}", file=sys.stderr)
    if result.get("bundle") is not None:
        print(json.dumps(result["bundle"], indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
