import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "pii_scan", Path(__file__).parents[1] / "pii_scan.py"
)
pii_scan = importlib.util.module_from_spec(_spec)
sys.modules["pii_scan"] = pii_scan
_spec.loader.exec_module(pii_scan)


def rules(text: str, denylist=()) -> list[str]:
    return [f.rule for f in pii_scan.scan_text("x.txt", text, denylist)]


@pytest.mark.parametrize(
    "text",
    [
        "call +1 202 555 0123 now",
        "(202) 555-0199",
        "+12025550150",
        "AC" + "0" * 32,
        "write to support@example.org",
        "git email 1234+someone@users.noreply.github.com",
        "sha256:3f0a1234567890abcdef1234567890abcdef",  # digit runs inside hex are not phones
        "timeout_s: 1234567890",  # a bare 10-digit number without separators is not a phone
        "uses: actions/checkout@v7.0.1",  # action refs are not e-mail addresses
    ],
)
def test_fictional_and_benign_values_pass(text):
    assert rules(text) == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("+1 312 555 2368", "phone-e164"),  # 555 exchange but not the 01xx block
        ("+13125552368", "phone-e164"),
        ("(312) 555-2368", "phone-us-formatted"),
        ("AC" + "1f" * 16, "twilio-sid"),
        ("projects/123456789012/locations/us", "gcp-project-number"),
        ("ping jane.doe@hospital.org", "email"),
        ("/Users/jdoe/code", "home-path"),
    ],
)
def test_generic_rules_flag_real_looking_values(text, rule):
    assert rule in rules(text)


def test_allow_marker_suppresses_generic_rules_only():
    line = "+13125552368  # pii-scan: allow"
    assert rules(line) == []
    assert rules(line, denylist=["3125552368"]) == ["denylist"]


def test_denylist_is_case_insensitive_and_masked():
    findings = list(pii_scan.scan_text("a.md", "Welcome to Contoso Clinic", ["contoso clinic"]))
    assert [f.rule for f in findings] == ["denylist"]
    assert "contoso" not in findings[0].sample.lower()
