"""Which system owns the shared SMS number (docs/sms-number-switch.md).

The number's incoming-message webhook is the switch: this system is active when it
points at our gateway's ``/twilio/sms``. Nothing else is stored; a flip is one
provider update plus an audit row here.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlsplit

from voa_core.config import resolve_secret
from voa_core.logging import mask_phone
from voa_core.store import new_id, now_iso

from .context import Api

log = logging.getLogger(__name__)

AUDIT = "sms_control_log"
CACHE_S = 30.0
SELF, PEER, UNKNOWN = "self", "peer", "unknown"


def normalise(url: str) -> tuple[str, str, str]:
    """(scheme, host, path) with the host lower-cased and a trailing / ignored."""
    u = urlsplit((url or "").strip())
    return (u.scheme.lower(), (u.hostname or "").lower(), u.path.rstrip("/"))


def own_webhook(api: Api) -> str:
    base = api.cfg.services.gateway_url.rstrip("/")
    return f"{base}/twilio/sms" if base else ""


def shared_number(api: Api) -> str:
    return api.cfg.sms.from_number or api.cfg.sms.voice_number


def owner_of(api: Api, sms_url: str) -> str:
    if sms_url and own_webhook(api) and normalise(sms_url) == normalise(own_webhook(api)):
        return SELF
    peer = api.cfg.sms.peer_webhook_url
    if sms_url and peer and normalise(sms_url) == normalise(peer):
        return PEER
    return UNKNOWN


def control(api: Api):
    """The provider handle for the number's settings (tests set ``api.number_control``)."""
    if not hasattr(api, "number_control"):
        from voa_providers.sms import TwilioNumberControl

        tw = api.cfg.providers.get("twilio", {})
        sec = {
            k: resolve_secret(tw.get(k, ""), api.cfg.gcp.project_id)
            for k in ("account_sid", "api_key_sid", "api_key_secret")
        }
        api.number_control = TwilioNumberControl(**sec) if all(sec.values()) else None
    return api.number_control


async def _read(api: Api) -> str:
    ctl = control(api)
    if ctl is None:
        raise RuntimeError("provider credentials are not configured")
    return await ctl.get_sms_url(shared_number(api))


async def status(api: Api, *, fresh: bool = False) -> dict[str, Any]:
    """Who has the number now. Cached for CACHE_S unless ``fresh``."""
    cached = getattr(api, "_sms_control_cache", None)
    if not fresh and cached and time.monotonic() - cached[0] < CACHE_S:
        return cached[1]
    s = api.cfg.sms
    number = shared_number(api)
    out: dict[str, Any] = {
        "enabled": s.switch_enabled,
        "self_name": s.self_name,
        "peer_name": s.peer_name,
        "number_last4": number[-4:] if number else "",
        "can_give": bool(s.peer_webhook_url),
        "dry_run": s.dry_run,
        "checked_at": now_iso(api.now()),
    }
    if not s.switch_enabled or not number:
        out.update(active=UNKNOWN, active_name=None, error="The SMS number switch is off.")
    else:
        try:
            url = await _read(api)
        except Exception as exc:  # noqa: BLE001 - fail closed, reported to the UI
            log.warning(
                "sms number read failed",
                extra={"event": "sms.control_read_failed", "error": type(exc).__name__},
            )
            out.update(active=UNKNOWN, active_name=None, error="Could not read the number.")
        else:
            who = owner_of(api, url)
            out.update(
                active=who,
                active_name={SELF: s.self_name, PEER: s.peer_name}.get(who),
                webhook_host=(urlsplit(url).hostname or "") if who == UNKNOWN else None,
            )
    api._sms_control_cache = (time.monotonic(), out)
    return out


async def open_conversations(api: Api) -> int:
    """This system's text conversations still open (a flip strands their replies)."""
    now = now_iso(api.now())
    rows = await api.store.query("sms_sessions")
    return sum(
        1
        for _, d in rows
        if d.get("state") not in (None, "closed", "expired", "opted_out")
        and str(d.get("expires_at") or "9999") > now
    )


async def is_active(api: Api) -> bool:
    """Send rule: real texts only while this system has the number. Fails closed."""
    if not api.cfg.sms.switch_enabled:
        return True  # not sharing a number: nothing to check
    return (await status(api))["active"] == SELF


async def flip(api: Api, action: str, *, by: str) -> dict[str, Any]:
    """``take`` points the number at us; ``give`` points it at the peer."""
    s = api.cfg.sms
    if not s.switch_enabled:
        raise ValueError("The SMS number switch is off.")
    target = own_webhook(api) if action == "take" else s.peer_webhook_url
    if not target:
        raise ValueError(
            "Our gateway URL is not configured."
            if action == "take"
            else f"{s.peer_name}'s webhook URL is not configured."
        )
    before = await status(api, fresh=True)
    ctl = control(api)
    if ctl is None:
        raise RuntimeError("provider credentials are not configured")
    await ctl.set_sms_url(shared_number(api), target)
    after = await status(api, fresh=True)
    await api.store.set(
        AUDIT,
        new_id(),
        {
            "at": now_iso(api.now()),
            "by": by,
            "action": action,
            "from": before.get("active_name") or before.get("active"),
            "to": after.get("active_name") or after.get("active"),
        },
    )
    log.info(
        "sms number switched",
        extra={
            "event": "sms.control_flip",
            "action": action,
            "to": after.get("active"),
            "number": mask_phone(shared_number(api)),
        },
    )
    return after
