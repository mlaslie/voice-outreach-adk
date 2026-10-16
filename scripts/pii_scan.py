#!/usr/bin/env python3
"""Fail if tracked (or staged) files contain identifying values.

Two layers:

1. **Denylist literals**: real org names, project ids, real phone digits, personal
   emails and so on. They come from ``config/pii-denylist.txt`` (gitignored) and/or
   the ``PII_DENYLIST`` environment variable (newline-separated; set as a CI
   secret). The real values therefore never appear in this repository.
2. **Generic patterns**: phone numbers outside the fictional 555-01xx range,
   Twilio account/key SIDs, GCP project numbers in resource paths, e-mail
   addresses outside an allowlist of example domains, and home-directory paths.

A line can opt out of the *generic* checks with the marker ``pii-scan: allow``.
Denylist hits can never be suppressed.

Usage::

    scripts/pii_scan.py                 # scan every tracked file
    scripts/pii_scan.py FILE [FILE...]  # scan the given files (pre-commit)
    scripts/pii_scan.py --staged        # scan files staged for commit

Exit status is 1 when anything is found. Findings print the file, line and the
rule name; the matched text is masked so the scanner itself never leaks it.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST_FILE = ROOT / "config" / "pii-denylist.txt"
ALLOW_MARKER = "pii-scan: allow"
MAX_BYTES = 2_000_000

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".ulaw", ".wav", ".lock"}
SKIP_NAMES = {"pii-denylist.example.txt"}
# The scanner's own tests contain realistic-looking fakes on purpose; denylist still applies.
GENERIC_EXEMPT_PATHS = {"scripts/tests/test_pii_scan.py"}

ALLOWED_EMAIL_DOMAINS = (
    "example.org",
    "example.com",
    "example.net",
    "users.noreply.github.com",
    "noreply.anthropic.com",
    "developer.gserviceaccount.com",
    "iam.gserviceaccount.com",  # templated SA addresses
)

_SEP = r"[\s.\-]?"
GENERIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "phone-e164",
        re.compile(
            r"(?<![\w+])\+\d{1,3}" + _SEP + r"\(?\d{3}\)?" + _SEP + r"\d{3}" + _SEP + r"\d{4}(?!\w)"
        ),
    ),
    ("phone-us-formatted", re.compile(r"(?<![\w+])\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\w)")),
    ("twilio-sid", re.compile(r"(?<!\w)(?:AC|SK|MG|PN)[0-9a-f]{32}(?!\w)")),
    ("gcp-project-number", re.compile(r"projects/\d{10,13}(?!\d)")),
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")),
    ("home-path", re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+")),
]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    sample: str


def _mask(text: str) -> str:
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


def _is_fictional_phone(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    national = digits[-10:]
    return national[3:6] == "555" and national[6:8] == "01"


def _generic_ok(rule: str, match: str) -> bool:
    if rule.startswith("phone"):
        return _is_fictional_phone(match)
    if rule == "twilio-sid":
        return set(match[2:]) <= {"0"}  # AC000...0 placeholder
    if rule == "email":
        domain = match.rsplit("@", 1)[-1].lower()
        return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)
    if rule == "home-path":
        return match.endswith(("/runner", "/USER", "/you", "/<user>"))
    return False


def load_denylist() -> list[str]:
    raw: list[str] = []
    if DENYLIST_FILE.is_file():
        raw += DENYLIST_FILE.read_text().splitlines()
    raw += os.environ.get("PII_DENYLIST", "").splitlines()
    items = {
        line.strip().lower() for line in raw if line.strip() and not line.lstrip().startswith("#")
    }
    return sorted(items, key=len, reverse=True)


def scan_text(path: str, text: str, denylist: Iterable[str]) -> Iterator[Finding]:
    deny = list(denylist)
    generic = path not in GENERIC_EXEMPT_PATHS
    for lineno, line in enumerate(text.splitlines(), start=1):
        lower = line.lower()
        for literal in deny:
            if literal in lower:
                yield Finding(path, lineno, "denylist", _mask(literal))
        if not generic or ALLOW_MARKER in line:
            continue
        for rule, pattern in GENERIC_RULES:
            for m in pattern.finditer(line):
                if not _generic_ok(rule, m.group(0)):
                    yield Finding(path, lineno, rule, _mask(m.group(0)))


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p]


def scan_paths(paths: Iterable[str], denylist: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for rel in paths:
        path = ROOT / rel
        # File names are scanned too (a number in a fixture file name leaks just as well).
        findings += scan_text(rel, rel, denylist) if rel not in SKIP_NAMES else []
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
            continue
        if path.stat().st_size > MAX_BYTES:
            findings.append(Finding(rel, 0, "too-large-to-scan", ""))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings += scan_text(rel, text, denylist)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="*")
    parser.add_argument("--staged", action="store_true", help="scan files staged for commit")
    parser.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail if no denylist is configured (use in CI)",
    )
    args = parser.parse_args(argv)

    denylist = load_denylist()
    if not denylist:
        msg = "pii_scan: no denylist (config/pii-denylist.txt or PII_DENYLIST); generic rules only"
        if args.require_denylist:
            print(msg, file=sys.stderr)
            return 2
        print(msg, file=sys.stderr)

    if args.files:
        paths = [os.path.relpath(Path(f).resolve(), ROOT) for f in args.files]
    elif args.staged:
        paths = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        paths = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")

    findings = scan_paths(paths, denylist)
    for f in findings:
        print(f"{f.path}:{f.line}: {f.rule} [{f.sample}]")
    if findings:
        print(
            f"pii_scan: {len(findings)} finding(s) in {len({f.path for f in findings})} file(s)",
            file=sys.stderr,
        )
        return 1
    print(f"pii_scan: clean ({len(paths)} files, {len(denylist)} denylist entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
