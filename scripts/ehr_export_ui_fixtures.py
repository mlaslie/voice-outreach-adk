#!/usr/bin/env python3
"""Regenerate the admin UI's EHR export sample-mode fixtures (fictional data only).

  uv run python scripts/ehr_export_ui_fixtures.py

Writes:

* ``ui/src/lib/ehr-export-sample.json``: the real engine's preview of the example
  profile on the fictional sample (``voa_ehr.exporter.preview``), as
  ``POST /api/ehr-export/preview`` returns it (docs/EHR-EXPORT-PLAN.md §4.2b).
* ``ui/src/lib/ehr-export-fixtures.json``: the example profile (as
  ``GET /api/ehr-export/example`` returns it), the sample outbox record, the
  questions per use case (the API's own ``questions`` route), the example's
  ``profile_problems``, and ``exporter.simulate`` on the sample.

The UI's sample-mode stub (``ui/src/lib/ehr-export-demo.ts``) is tested against
these, so rerun this after changing the engine.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from voa_api.routes_ehr_export import questions
from voa_core.campaign import campaign_ids
from voa_ehr import exporter
from voa_ehr.profile import parse_profile, profile_from_text, profile_problems

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/ehr/profiles/post_op.example.yaml"
SAMPLE = ROOT / "config/ehr/samples/post_op.outbox.json"
OUT = ROOT / "ui/src/lib"


NOW = datetime(2026, 10, 22, 9, tzinfo=UTC)


def main() -> None:
    # As GET /api/ehr-export/example returns it: the full canonical JSON.
    raw_profile = profile_from_text(PROFILE.read_text()).model_dump(mode="json")
    profile = parse_profile(raw_profile)
    data = json.loads(SAMPLE.read_text())
    call_id, record, call = data["call_id"], data["record"], data["call"]
    result = asyncio.run(exporter.preview(None, profile, record, call_id=call_id, call=call))
    # The route adds where the profile came from (body | draft | active | example).
    preview = {**result, "profile_source": "example"}
    (OUT / "ehr-export-sample.json").write_text(json.dumps(preview, indent=2) + "\n")
    fixtures = {
        "_comment": "FICTIONAL. From scripts/ehr_export_ui_fixtures.py; do not edit.",
        "example_profile": raw_profile,
        "sample": {"campaign_id": "post_op", "call_id": call_id, "record": record, "call": call},
        "questions": {cid: asyncio.run(questions(campaign_id=cid)) for cid in campaign_ids()},
        "example_problems": [p.as_dict() for p in profile_problems(profile)],
        "simulate": asyncio.run(
            exporter.simulate(profile, record, call_id=call_id, call=call, now=NOW)
        ),
    }
    (OUT / "ehr-export-fixtures.json").write_text(json.dumps(fixtures, indent=2) + "\n")
    print(f"source_map: {len(preview['source_map'])} links")


if __name__ == "__main__":
    main()
