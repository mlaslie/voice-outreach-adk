"""Short-lived signed links to one call's recording (played inside the nurse
agent's card in Gemini Enterprise).

The private API mints the link; the public gateway serves the WAV only for a
valid, unexpired signature. The signed message is prefixed ("rec.") so a
browser test-call token, signed with the same key, can never open a recording.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from urllib.parse import quote

RECORDING_LINK_TTL_S = 15 * 60
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sig(key: str, call_id: str, exp: int) -> str:
    return hmac.new(key.encode(), f"rec.{call_id}.{exp}".encode(), hashlib.sha256).hexdigest()


def recording_link(
    gateway_url: str,
    key: str,
    call_id: str,
    *,
    now: float | None = None,
    ttl_s: int = RECORDING_LINK_TTL_S,
) -> tuple[str, int]:
    """(url, expiry unix seconds)."""
    if not _CALL_ID.match(call_id):
        raise ValueError("bad call id")
    exp = int((now or time.time()) + ttl_s)
    base = f"{gateway_url.rstrip('/')}/recording/{quote(call_id)}"
    return f"{base}?exp={exp}&sig={_sig(key, call_id, exp)}", exp


def verify_recording_link(
    key: str, call_id: str, exp: str, sig: str, *, now: float | None = None
) -> bool:
    if not (key and _CALL_ID.match(call_id or "") and (exp or "").isdigit() and sig):
        return False
    if int(exp) < (now or time.time()):
        return False
    return hmac.compare_digest(_sig(key, call_id, int(exp)), sig)
