#!/usr/bin/env python3
"""Load the fictional sample Q&A into the knowledge base, as DRAFTS.

Reads ``config/knowledge/sample_items.yaml``, signs in as the ``voa-admin``
service account (the admin console's identity; operators may impersonate it, see
infra), and POSTs each item to /api/admin/knowledge/items. Nothing is approved:
a clinician reviews and approves each one in Admin -> Knowledge base. Items whose
question is already there are skipped, so re-running is safe.

  uv run python scripts/seed_knowledge.py demo [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "config/knowledge/sample_items.yaml"
FIELDS = ("question", "answer", "category", "alt_questions")


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


def call(method: str, url: str, tok: str, body: dict | None = None) -> dict:
    if not url.startswith("https://"):
        sys.exit(f"refusing a non-https API URL: {url}")
    req = urllib.request.Request(  # noqa: S310 (https only, checked above)
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "X-Acting-User": "seed_knowledge.py",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url}: {e.code}: {e.read().decode()[:500]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("env")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent")
    a = ap.parse_args()

    items = [
        {k: it[k] for k in FIELDS if k in it} for it in yaml.safe_load(SAMPLE.read_text())["items"]
    ]
    if a.dry_run:
        print(json.dumps(items, indent=2))
        return 0

    api = tf_output(a.env, "api_url").strip()
    sa = json.loads(tf_output(a.env, "service_accounts", raw=False))["voa-admin"]
    base = f"{api}/api/admin/knowledge/items"
    tok = token(sa, api)
    have = {i["question"] for i in call("GET", base, tok)["items"]}
    for it in items:
        if it["question"] in have:
            print(f"  skip (already there): {it['question']}")
            continue
        out = call("POST", base, tok, it)
        print(f"  draft v{out['version']} [{out['category']}]: {out['question']}")
    print("Approve them in Admin -> Knowledge base. Nothing is said to patients until then.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
