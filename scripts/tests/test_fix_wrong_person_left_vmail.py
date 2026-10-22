"""The one-off correction for 20 Oct's wrong-person voicemails. Fictional data only."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fix_wrong_person_left_vmail", Path(__file__).parents[1] / "fix_wrong_person_left_vmail.py"
)
fix = importlib.util.module_from_spec(_spec)
sys.modules["fix_wrong_person_left_vmail"] = fix
_spec.loader.exec_module(fix)

WRONG = {"call_status": "left_vmail", "outcome": "unable_to_reach_wrong_person", "channel": "voice"}
DROP = ("r-drop", {"outcome": "voicemail_left", "follow_up": "none", "created_at": "T1"})
AGENT = ("r-agent", {"outcome": "unable_to_reach_wrong_person", "created_at": "T2"})


def test_a_dropped_voicemail_gets_its_outcome_back():
    kind, patch = fix.plan(WRONG, [AGENT, DROP])
    assert kind == "drop_overwritten"
    assert patch == {"outcome": "voicemail_left", "follow_up": "none", "report_doc_id": "r-drop"}


def test_an_inferred_voicemail_becomes_interrupted():
    assert fix.plan(WRONG, [AGENT]) == ("inferred_voicemail", {"call_status": "interrupted"})


def test_everything_else_is_left_alone():
    assert fix.plan({**WRONG, "channel": "sms"}, [AGENT]) == ("sms_outcome", {})
    assert fix.plan({**WRONG, "outcome": "voicemail_left"}, [DROP]) == ("ok", {})
    assert fix.plan({**WRONG, "call_status": "interrupted"}, []) == ("not_left_vmail", {})
