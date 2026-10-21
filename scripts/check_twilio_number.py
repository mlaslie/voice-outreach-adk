#!/usr/bin/env python3
"""Read-only check of the Twilio number's webhooks (docs/RUNBOOK.md).

This system owns the number outright, so its settings must be:

  SmsUrl   = <gateway_url>/twilio/sms, method POST
  VoiceUrl = empty (outbound calls set their own TwiML; there is no inbound voice)
  no SMS application on the number (it would override SmsUrl)

Nothing is written. The Twilio credentials come from Secret Manager (the same
secrets the services use); the number and project come from the env's
``terraform.tfvars`` and the gateway URL from ``terraform output``. Only the
URLs and the number's last 4 digits are printed. Exit code 1 when a check fails.

  uv run python scripts/check_twilio_number.py demo
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tfvar(text: str, name: str) -> str:
    m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', text, flags=re.M)
    return m.group(1) if m else ""


def problems(
    *, sms_url: str, sms_method: str, voice_url: str, sms_app: bool, gateway_url: str
) -> list[str]:
    """What is wrong with the number's settings; empty when it is as expected."""
    want = gateway_url.rstrip("/") + "/twilio/sms"
    out = []
    if (sms_url or "").rstrip("/") != want:
        out.append(f"SmsUrl should be {want}")
    if (sms_method or "").upper() != "POST":
        out.append("SmsMethod should be POST")
    if voice_url:
        out.append("VoiceUrl should be empty (this system has no inbound voice)")
    if sms_app:
        out.append("an SMS application is set on the number; it overrides SmsUrl")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("env", help="Terraform env under infra/envs, e.g. demo")
    a = ap.parse_args()

    env_dir = ROOT / "infra/envs" / a.env
    tfvars = (env_dir / "terraform.tfvars").read_text()
    project = tfvar(tfvars, "project_id")
    number = tfvar(tfvars, "sms_from_number") or tfvar(tfvars, "twilio_from_number")
    if not project or not number:
        sys.exit("project_id and sms_from_number / twilio_from_number must be set in tfvars")
    gateway_url = subprocess.run(
        ["terraform", f"-chdir={env_dir}", "output", "-raw", "gateway_url"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    sys.path.insert(0, str(ROOT / "packages/voa_core/src"))
    from twilio.rest import Client
    from voa_core.config import resolve_secret

    sid, key, secret = (
        resolve_secret(f"secret://twilio-{n}", project)
        for n in ("account-sid", "api-key-sid", "api-key-secret")
    )
    found = Client(key, secret, sid).incoming_phone_numbers.list(phone_number=number, limit=1)
    print(f"number   ...{number[-4:]}")
    if not found:
        print("not found on this Twilio account")
        return 1
    n = found[0]
    print(f"SmsUrl   {n.sms_url or '(empty)'} ({n.sms_method or '-'})")
    print(f"VoiceUrl {n.voice_url or '(empty)'}")
    print(f"SMS app  {'set' if n.sms_application_sid else 'none'}")
    bad = problems(
        sms_url=n.sms_url or "",
        sms_method=n.sms_method or "",
        voice_url=n.voice_url or "",
        sms_app=bool(n.sms_application_sid),
        gateway_url=gateway_url,
    )
    for p in bad:
        print(f"FAIL: {p}")
    if not bad:
        print("OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
