#!/usr/bin/env python3
"""Send a fictional EHR batch to the deployed API, as the EHR feed would.

Builds one batch of fictional patients (morning and afternoon tranches) on the
numbers you pass, signs in as the ``voa-ingest`` service account (operators may
impersonate it; see infra), PUTs it to /api/v1/call-batches, and prints the
per-item result. Re-running with the same --batch-id is a no-op (idempotent).

  uv run python scripts/sample_ehr_feed.py demo --phone +1… --phone +1… \
      [--batch-id 2026-10-21] [--date 2026-10-21] [--withdraw]

Numbers must be authorized in the admin console or they come back "rejected".
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = [
    ("Jordan Rivera", "1950-03-15", "Total knee replacement", False),
    ("Alex Morgan", "1962-07-04", "Hip arthroplasty", True),
    ("Sam Taylor", "1971-11-23", "Laparoscopic cholecystectomy", False),
    ("Casey Lee", "1958-02-09", "Rotator cuff repair", False),
]


def tf_output(env: str, name: str, raw: bool = True) -> str:
    args = ["terraform", f"-chdir=infra/envs/{env}", "output"] + (["-raw"] if raw else ["-json"])
    return subprocess.run(
        [*args, name], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def token(sa: str, audience: str) -> str:
    gcloud = shutil.which("gcloud") or str(Path.home() / "Downloads/google-cloud-sdk/bin/gcloud")
    out = subprocess.run(
        [
            gcloud,
            "auth",
            "print-identity-token",
            f"--impersonate-service-account={sa}",
            f"--audiences={audience}",
            "--include-email",
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(
            f"could not sign in as {sa} (operators need tokenCreator on it): "
            f"{out.stderr.strip().splitlines()[-1] if out.stderr else ''}"
        )
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("env")
    ap.add_argument("--phone", action="append", required=True, help="authorized E.164 number")
    ap.add_argument("--date", default=(date.today() + timedelta(days=1)).isoformat())
    ap.add_argument("--batch-id")
    ap.add_argument("--source", default="sample-ehr")
    ap.add_argument("--tz-offset", default="-04:00", help="clinic UTC offset for the tranches")
    ap.add_argument("--withdraw", action="store_true", help="cancel the batch instead")
    a = ap.parse_args()

    api = tf_output(a.env, "api_url").strip()
    sa = json.loads(tf_output(a.env, "service_accounts", raw=False))["voa-ingest"]
    batch_id = a.batch_id or a.date
    url = f"{api}/api/v1/call-batches/{a.source}/{batch_id}"
    calls = []
    for i, phone in enumerate(a.phone):
        name, dob, proc, diabetic = NAMES[i % len(NAMES)]
        calls.append(
            {
                "external_id": f"enc-{batch_id}-{i + 1}",
                "mrn": f"SAMPLE-{i + 1:04d}",
                "name": name,
                "dob": dob,
                "phone": phone,
                "procedure": proc,
                "procedure_date": (date.fromisoformat(a.date) - timedelta(days=1)).isoformat(),
                "is_diabetic": diabetic,
            }
        )
    half = (len(calls) + 1) // 2
    body = {
        "tranches": [
            {
                "label": "Morning",
                "scheduled_for": f"{a.date}T10:00:00{a.tz_offset}",
                "calls": calls[:half],
            },
            *(
                [
                    {
                        "label": "Afternoon",
                        "scheduled_for": f"{a.date}T14:00:00{a.tz_offset}",
                        "calls": calls[half:],
                    }
                ]
                if calls[half:]
                else []
            ),
        ]
    }
    if not url.startswith("https://"):
        sys.exit(f"refusing a non-https API URL: {url}")
    req = urllib.request.Request(  # noqa: S310 (https only, checked above)
        url,
        method="DELETE" if a.withdraw else "PUT",
        data=None if a.withdraw else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token(sa, api)}",
            "Content-Type": "application/json",
            "X-Acting-User": f"sample-ehr-feed:{a.source}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            out = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{e.code}: {e.read().decode()[:500]}")
    print(json.dumps({k: out.get(k) for k in ("status", "replay", "counts", "runs")}, indent=2))
    for i in out.get("items", out.get("results", [])):
        print(
            f"  {i.get('external_id', i.get('entry_id'))}: {i['status']}"
            + (f" ({i['reason']})" if i.get("reason") else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
