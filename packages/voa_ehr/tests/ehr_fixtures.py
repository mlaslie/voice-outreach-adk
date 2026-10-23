"""Shared fictional fixtures for the voa_ehr tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from voa_ehr.profile import Profile, parse_profile, profile_from_text

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PROFILE = ROOT / "config/ehr/profiles/post_op.example.yaml"
SAMPLE = ROOT / "config/ehr/samples/post_op.outbox.json"

MRN_SYSTEM = "https://fhir.example.org/identifiers/mrn"
CALL_SYSTEM = "https://voice-outreach.example.org/identifiers/call"
#: Fictional DOB put on the call doc in tests: it must never reach the EHR.
DOB = "1950-03-15"


def example_profile() -> Profile:
    return profile_from_text(EXAMPLE_PROFILE.read_text())


def example_data() -> dict[str, Any]:
    return profile_from_text(EXAMPLE_PROFILE.read_text()).model_dump(mode="json")


def profile_with(**changes: Any) -> Profile:
    """The example with top-level or use_cases[0] keys replaced.

    ``uc__<key>=value`` replaces a key of use_cases[0]; ``qr__items=...`` a key
    of its questionnaire_response; anything else is top level.
    """
    data = example_data()
    uc = data["use_cases"][0]
    for k, v in changes.items():
        if k.startswith("uc__"):
            uc[k[4:]] = v
        elif k.startswith("qr__"):
            uc["questionnaire_response"][k[4:]] = v
        elif k.startswith("task__"):
            uc["task"][k[6:]] = v
        elif k.startswith("patient__"):
            data["patient"][k[9:]] = v
        elif k.startswith("target__"):
            data["target"][k[8:]] = v
        else:
            data[k] = v
    return parse_profile(data)


def sample() -> tuple[str, dict[str, Any], dict[str, Any]]:
    """(call_id, outbox record, call doc), fresh copies."""
    data = json.loads(SAMPLE.read_text())
    return data["call_id"], copy.deepcopy(data["record"]), copy.deepcopy(data["call"])


def record_with_answers(answers: dict[str, Any], **extra: Any) -> dict[str, Any]:
    _, rec, _ = sample()
    rec["questionnaire_response"]["item"] = [{"linkId": k, "answer": v} for k, v in answers.items()]
    rec.update(extra)
    return rec
