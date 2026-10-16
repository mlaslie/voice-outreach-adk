"""Structured JSON logging with call correlation and PII masking.

Every record is one JSON line on stdout. Cloud Run / Cloud Logging parses
``severity``, ``message``, ``logging.googleapis.com/trace`` and
``logging.googleapis.com/labels`` natively; everything else lands in
``jsonPayload`` and is queryable.

Correlation fields (``call_id``, ``session_id``, ``campaign``, ``provider``,
``trace``) come from context variables, so code deep in a call does not have to
thread them through: set them once with :func:`bind` at the top of a request or
media session.

Use the ``event`` keyword for the dotted event taxonomy (``amd.verdict``,
``live.connected``, ``drain.mark_ack`` ...)::

    log.info("agent connected", extra={"event": "live.connected", "latency_ms": 412})
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_CTX_FIELDS = ("call_id", "session_id", "campaign", "provider", "trace", "span_id")
_ctx: dict[str, contextvars.ContextVar[str | None]] = {
    name: contextvars.ContextVar(f"voa_{name}", default=None) for name in _CTX_FIELDS
}

# Attributes every LogRecord has; anything else passed via ``extra=`` is ours.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}

_PROJECT_ID: str | None = None


@contextmanager
def bind(**fields: str | None) -> Iterator[None]:
    """Bind correlation fields for the current task (and tasks it spawns)."""
    tokens = []
    for key, value in fields.items():
        if key not in _ctx:
            raise KeyError(f"unknown log context field: {key}")
        tokens.append((_ctx[key], _ctx[key].set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def set_context(**fields: str | None) -> None:
    """Like :func:`bind` but without automatic reset (for long-lived tasks)."""
    for key, value in fields.items():
        _ctx[key].set(value)


# --------------------------------------------------------------------------- masking

_E164_RE = re.compile(r"\+?\d[\d\-\s().]{8,}\d")


def mask_phone(number: str | None) -> str:
    """``+12025550123`` -> ``+1***0123``. Short codes are masked entirely."""
    if not number:
        return "<none>"
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 5:
        return "<invalid>"
    prefix = "+" if number.strip().startswith("+") else ""
    if len(digits) < 10:
        return f"{prefix}<{len(digits)}-digit-number>"
    return f"{prefix}{digits[: len(digits) - 10]}***{digits[-4:]}"


def scrub_text(text: str) -> str:
    """Best-effort removal of phone numbers from free text (log messages, errors)."""
    return _E164_RE.sub(lambda m: mask_phone(m.group(0)), text)


# --------------------------------------------------------------------------- formatter


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": scrub_text(record.getMessage()),
            "logger": record.name,
        }
        labels: dict[str, str] = {}
        for name in _CTX_FIELDS:
            value = _ctx[name].get()
            if value:
                if name == "trace":
                    payload["logging.googleapis.com/trace"] = (
                        f"projects/{_PROJECT_ID}/traces/{value}" if _PROJECT_ID else value
                    )
                elif name == "span_id":
                    payload["logging.googleapis.com/spanId"] = value
                else:
                    payload[name] = value
                    labels[name] = value
        if labels:
            payload["logging.googleapis.com/labels"] = labels
        for key, value in vars(record).items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value if _jsonable(value) else repr(value)
        if record.exc_info:
            payload["exception"] = scrub_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def _jsonable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))


def configure_logging(
    level: str = "INFO", project_id: str | None = None, *, json_output: bool = True
) -> None:
    """Install one stdout handler on the root logger. Idempotent."""
    global _PROJECT_ID
    _PROJECT_ID = project_id
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    # Third-party loggers that log request URLs/bodies at INFO.
    for noisy in ("httpx", "httpcore", "websockets", "twilio.http_client", "google_genai"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def parse_cloud_trace_header(header: str | None) -> tuple[str | None, str | None]:
    """``X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`` -> (trace_id, span_id)."""
    if not header:
        return None, None
    trace, _, rest = header.partition("/")
    span = rest.split(";", 1)[0] or None
    return (trace or None), span
