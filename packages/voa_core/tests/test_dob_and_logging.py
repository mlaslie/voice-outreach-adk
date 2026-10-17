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


def test_safe_extra_never_breaks_logging(caplog):
    from voa_core.logging import safe_extra

    log = logging.getLogger("voa.test")
    with caplog.at_level(logging.INFO, logger="voa.test"):
        log.info(
            "tool", extra=safe_extra({"event": "tool.call", "name": "verify_identity", "args": 1})
        )
    rec = caplog.records[-1]
    assert rec.name == "voa.test" and rec.name_ == "verify_identity" and rec.args_ == 1


async def test_gateway_timeline_logs_at_info(caplog):
    from voa_gateway.session import Timeline

    with caplog.at_level(logging.INFO):
        Timeline().add("tool.call", name="end_call", msg="x")
