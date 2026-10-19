"""Process-wide dependencies for the API, built once at startup."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from voa_core.config import AppConfig, resolve_secret
from voa_core.store import DocStore, FirestoreStore, MemoryStore
from voa_core.tasks import CloudTaskQueue, MemoryTaskQueue, TaskQueue

log = logging.getLogger(__name__)

# Collections
CALLS, PENDING, REPORTS, TRANSCRIPTS, EVENTS = (
    "calls",
    "pending_calls",
    "reports",
    "transcripts",
    "call_events",
)
CALL_LIST, CALL_RUNS, LIST_RUNS, SETTINGS, ALLOWLIST = (
    "call_list",
    "call_runs",
    "list_runs",
    "call_settings",
    "outbound_allowlist",
)
#: Signed-off results waiting for the EHR integration (one per call).
EHR_OUTBOX = "ehr_outbox"

# smstest_: a call doc staged by the dry-run SMS simulator (sms_simulator.py).
CALL_ID_RE = re.compile(
    r"^(CA[0-9a-f]{32}|browser_[0-9a-f]{16}|local_[0-9a-f]{16,32}|smstest_[0-9a-f]{16})$"
)
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class Api:
    cfg: AppConfig
    store: DocStore
    tasks: TaskQueue
    voice: Any = None  # VoiceProvider; None = telephony not configured
    clock: Any = field(default=lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self.clock()

    @classmethod
    def build(cls, cfg: AppConfig) -> Api:
        store: DocStore = (
            MemoryStore() if cfg.services.store == "memory" else FirestoreStore(cfg.gcp.project_id)
        )
        if cfg.services.api_url and cfg.services.tasks_invoker_sa:
            tasks: TaskQueue = CloudTaskQueue(
                project_id=cfg.gcp.project_id,
                region=cfg.gcp.region,
                target_base_url=cfg.services.api_url,
                invoker_sa=cfg.services.tasks_invoker_sa,
            )
        else:
            tasks = MemoryTaskQueue()
        return cls(cfg=cfg, store=store, tasks=tasks, voice=_build_voice(cfg))


def _build_voice(cfg: AppConfig):
    """The configured voice provider, or None when its secrets aren't set yet
    (dialing then fails closed with 503 instead of crashing at startup)."""
    if cfg.telephony.voice_provider != "twilio":
        return None
    tw = cfg.providers.get("twilio", {})
    try:
        from voa_providers.twilio import TwilioVoice

        sec = {
            k: resolve_secret(tw.get(k, ""), cfg.gcp.project_id)
            for k in ("account_sid", "api_key_sid", "api_key_secret")
        }
        if not all(sec.values()) or not tw.get("from_number"):
            raise ValueError("incomplete twilio config")
        return TwilioVoice(from_number=tw["from_number"], **sec)
    except Exception:
        log.warning(
            "voice provider not configured; dialing disabled",
            extra={"event": "telephony.not_configured"},
        )
        return None
