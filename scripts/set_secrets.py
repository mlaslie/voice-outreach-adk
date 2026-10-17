#!/usr/bin/env python3
"""Load Twilio credentials into Secret Manager (and the caller ID into tfvars).

Values never touch the repo, argv, logs or Terraform state: they are read from
the sources below and piped to ``gcloud secrets versions add --data-file=-``.

Sources, first match wins, per secret:
  1. environment: VOA_TWILIO_ACCOUNT_SID, VOA_TWILIO_API_KEY_SID,
     VOA_TWILIO_API_KEY_SECRET, VOA_TWILIO_AUTH_TOKEN, VOA_TWILIO_FROM_NUMBER
  2. --from-env FILE: a dotenv file. Understands both plain names and the old
     system's names (where TWILIO_ACCOUNT_SID held the API *key* SID and
     TWILIO_AUTH_TOKEN held the API key *secret*); values are told apart by shape.
  3. --auth-token-from projects/<p>/secrets/<name>: copy the auth token from an
     existing secret (e.g. the previous system's project)
  4. an interactive hidden prompt (skipped with --no-prompt)

Every value is shape-checked before upload (AC…/SK… + 32 hex, 32-char secrets,
E.164 numbers), so a misplaced value is refused rather than stored.

Usage:
  scripts/set-secrets.sh <project-id> [--from-env FILE] [--auth-token-from SECRET]
                         [--tfvars infra/envs/demo/terraform.tfvars] [--no-prompt] [--dry-run]
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HEX32 = r"[0-9a-f]{32}"
SHAPES = {
    "twilio-account-sid": (re.compile(rf"AC{HEX32}"), "AC + 32 hex"),
    "twilio-api-key-sid": (re.compile(rf"SK{HEX32}"), "SK + 32 hex"),
    "twilio-api-key-secret": (re.compile(r"[A-Za-z0-9]{32}"), "32 letters/digits"),
    "twilio-auth-token": (re.compile(r"[0-9a-f]{32}"), "32 hex"),
}
ENV_VARS = {
    "twilio-account-sid": "VOA_TWILIO_ACCOUNT_SID",
    "twilio-api-key-sid": "VOA_TWILIO_API_KEY_SID",
    "twilio-api-key-secret": "VOA_TWILIO_API_KEY_SECRET",
    "twilio-auth-token": "VOA_TWILIO_AUTH_TOKEN",
}
E164 = re.compile(r"\+[1-9]\d{9,14}")


def parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def from_dotenv(env: dict[str, str]) -> dict[str, str]:
    """Map a dotenv onto secret names by value shape (handles the old naming)."""
    found: dict[str, str] = {}
    sid_like = [v for k, v in env.items() if "SID" in k]
    for v in sid_like:
        if re.fullmatch(rf"AC{HEX32}", v):
            found.setdefault("twilio-account-sid", v)
        elif re.fullmatch(rf"SK{HEX32}", v):
            found.setdefault("twilio-api-key-sid", v)
    for key in ("TWILIO_API_KEY_SECRET", "TWILIO_API_SECRET"):
        if env.get(key):
            found.setdefault("twilio-api-key-secret", env[key])
    if env.get("TWILIO_PRIMARY_AUTH_TOKEN"):
        found.setdefault("twilio-auth-token", env["TWILIO_PRIMARY_AUTH_TOKEN"])
    token = env.get("TWILIO_AUTH_TOKEN", "")
    if token:
        # Paired with an API key SID, TWILIO_AUTH_TOKEN is that key's secret (old
        # system's naming). Paired with only an account SID it is the auth token.
        if "twilio-api-key-sid" in found and "twilio-api-key-secret" not in found:
            found["twilio-api-key-secret"] = token
        elif "twilio-api-key-sid" not in found:
            found.setdefault("twilio-auth-token", token)
    for key in ("TWILIO_FROM_NUMBER", "TWILIO_VOICE_NUMBER"):
        if E164.fullmatch(env.get(key, "")):
            found.setdefault("from_number", env[key])
            break
    return found


_GCLOUD: str | None = None


def find_gcloud() -> str:
    """gcloud from PATH, $CLOUDSDK_ROOT_DIR, or the usual install locations (an IDE or
    app shell often lacks the PATH entry the installer added to your login shell)."""
    global _GCLOUD
    if _GCLOUD:
        return _GCLOUD
    candidates = [shutil.which("gcloud")]
    roots = [
        os.environ.get("CLOUDSDK_ROOT_DIR", ""),
        "~/google-cloud-sdk",
        "~/Downloads/google-cloud-sdk",
        "/opt/homebrew/share/google-cloud-sdk",
        "/usr/local/share/google-cloud-sdk",
        "/usr/lib/google-cloud-sdk",
    ]
    candidates += [str(Path(r).expanduser() / "bin" / "gcloud") for r in roots if r]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            _GCLOUD = c
            return c
    sys.exit(
        "gcloud not found. Install the Google Cloud CLI, put it on PATH, or set "
        "CLOUDSDK_ROOT_DIR to the SDK folder (the one containing bin/gcloud)."
    )


def gcloud(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([find_gcloud(), *args], input=stdin, capture_output=True, text=True)  # noqa: S603


def copy_from_secret(resource: str) -> str | None:
    m = re.fullmatch(r"projects/([^/]+)/secrets/([^/]+)(?:/versions/(\w+))?", resource)
    if not m:
        sys.exit("--auth-token-from must look like projects/<project>/secrets/<name>")
    r = gcloud(
        "secrets",
        "versions",
        "access",
        m.group(3) or "latest",
        f"--secret={m.group(2)}",
        f"--project={m.group(1)}",
    )
    if r.returncode != 0:
        print(f"  could not read {resource}: {last_err(r)}")
        return None
    return r.stdout.strip()


def last_err(r: subprocess.CompletedProcess) -> str:
    return r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "error"


def masked(v: str) -> str:
    return f"{v[:2]}…{v[-2:]} ({len(v)} chars)" if len(v) > 6 else "***"


def update_tfvars(path: Path, number: str, dry_run: bool) -> None:
    text = path.read_text() if path.exists() else ""
    line = f'twilio_from_number = "{number}"'
    if re.search(r"^twilio_from_number\s*=.*$", text, flags=re.M):
        new = re.sub(r"^twilio_from_number\s*=.*$", line, text, flags=re.M)
    else:
        new = text.rstrip("\n") + "\n" + line + "\n"
    if dry_run:
        print(f"  [dry-run] would set twilio_from_number in {path}")
        return
    path.write_text(new)
    print(f"  set twilio_from_number in {path} (+{number[1:2]}…{number[-4:]})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("project_id")
    ap.add_argument("--from-env", type=Path)
    ap.add_argument("--auth-token-from", metavar="SECRET")
    ap.add_argument("--tfvars", type=Path)
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="validate and report; upload nothing")
    a = ap.parse_args()

    dotenv = from_dotenv(parse_dotenv(a.from_env)) if a.from_env else {}
    missing: list[str] = []
    uploaded = 0
    print(f"Secret Manager project: {a.project_id}")
    for name, (shape, desc) in SHAPES.items():
        value, source = os.environ.get(ENV_VARS[name], ""), "environment"
        if not value and name in dotenv:
            value, source = dotenv[name], str(a.from_env)
        if not value and name == "twilio-auth-token" and a.auth_token_from:
            value, source = copy_from_secret(a.auth_token_from) or "", a.auth_token_from
        if not value and not a.no_prompt and sys.stdin.isatty():
            value, source = getpass.getpass(f"{name} ({desc}; empty to skip): ").strip(), "prompt"
        if not value:
            print(f"- {name}: MISSING")
            missing.append(name)
            continue
        if not shape.fullmatch(value):
            print(f"- {name}: REFUSED from {source} (expected {desc}, got {masked(value)})")
            missing.append(name)
            continue
        current = gcloud(
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={name}",
            f"--project={a.project_id}",
        )
        if current.returncode == 0 and current.stdout.strip() == value:
            print(f"- {name}: unchanged (latest version already holds this value)")
            continue
        if a.dry_run:
            print(f"- {name}: ok from {source} [dry-run, not uploaded]")
            continue
        r = gcloud(
            "secrets",
            "versions",
            "add",
            name,
            f"--project={a.project_id}",
            "--data-file=-",
            stdin=value,
        )
        if r.returncode != 0:
            print(f"- {name}: UPLOAD FAILED: {last_err(r)}")
            missing.append(name)
        else:
            uploaded += 1
            print(f"- {name}: added a version (from {source})")

    number = os.environ.get("VOA_TWILIO_FROM_NUMBER") or dotenv.get("from_number")
    if a.tfvars:
        if number and E164.fullmatch(number):
            update_tfvars(a.tfvars, number, a.dry_run)
        else:
            print(
                "- twilio_from_number: MISSING "
                "(set VOA_TWILIO_FROM_NUMBER, or TWILIO_FROM_NUMBER in the env file)"
            )
            missing.append("twilio_from_number")

    if missing:
        print("\nStill needed: " + ", ".join(missing))
        return 1
    if uploaded:
        print("\nAll set. Restart the API so it picks up the credentials (see docs/DEPLOY.md §3).")
    else:
        print("\nAll set; nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
