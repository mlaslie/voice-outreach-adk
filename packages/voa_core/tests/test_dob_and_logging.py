import json
import logging

from voa_core.logging import JsonFormatter, bind, mask_phone, scrub_text


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
