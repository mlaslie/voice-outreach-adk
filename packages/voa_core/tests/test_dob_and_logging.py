import json
import logging

import pytest
from voa_core.dob import DobResult, check_dob, parse_dob
from voa_core.logging import JsonFormatter, bind, mask_phone, scrub_text


@pytest.mark.parametrize(
    "spoken",
    [
        "March 15th, 1950",
        "march fifteenth 1950".replace("fifteenth", "15"),
        "3/15/1950",
        "1950-03-15",
        "15 March 1950",
    ],
)
def test_dob_forms_match(spoken):
    assert check_dob(spoken, "1950-03-15").result is DobResult.MATCH


def test_dob_mismatch_and_unparseable():
    assert check_dob("June 2nd 1961", "1950-03-15").result is DobResult.MISMATCH
    assert check_dob("March 15th", "1950-03-15").result is DobResult.UNPARSEABLE  # no year: re-ask
    assert parse_dob("February 30 1950") is None


def test_mask_and_scrub():
    assert mask_phone("+12025550123") == "+1***0123"
    assert mask_phone("12345") == "<5-digit-number>"
    assert "5550123" not in scrub_text("dialing +1 202 555 0123 now")


def test_json_formatter_carries_context_and_masks():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "call to +12025550123", None, None)
    rec.event = "dial.requested"
    with bind(call_id="CA_test", campaign="post_op"):
        out = json.loads(JsonFormatter().format(rec))
    assert out["event"] == "dial.requested"
    assert out["call_id"] == "CA_test"
    assert out["logging.googleapis.com/labels"]["campaign"] == "post_op"
    assert "5550123" not in out["message"]
