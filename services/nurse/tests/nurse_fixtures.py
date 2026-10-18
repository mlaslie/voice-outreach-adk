"""Shared test helpers for voa_nurse: fictional datasets, an in-memory fake of the
outreach API client, a scripted model, and A2A request/queue helpers.

Fictional data only: Acme Health patients, phone numbers in +1 202 555 0100-0199,
emails at example.org. Every dataset is built relative to "now", so "today"
always has content and nothing scheduled is in the past.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import DataPart, Message, MessageSendParams, Part, Role, TextPart
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types
from pydantic import Field
from voa_core.config import AppConfig
from voa_core.store import MemoryStore
from voa_nurse import cards, schema, turn
from voa_nurse.backend import BackendError
from voa_nurse.executor import EXT_V09
from voa_nurse.proposals import ProposalStore
from voa_nurse.settings import NurseSettings

CLINIC_TZ = ZoneInfo("America/New_York")


def make_cfg(**services) -> AppConfig:
    return AppConfig.model_validate(
        {
            "env": "test",
            "gcp": {"project_id": "acme-demo-project"},
            "services": {"store": "memory", "api_url": "http://localhost:9", **services},
            "site": {"timezone": "America/New_York"},
        }
    )


def make_settings(**over) -> NurseSettings:
    base = {
        "public_url": "https://nurse.example.org",
        "dashboard_url": "https://dashboard.example.org",
        "heartbeat_s": 1.0,
    }
    return NurseSettings(**{**base, **over})


def check(card: cards.Card) -> list[dict]:
    """Schema-valid A2UI v0.9, no dangling ids, never a blank text fallback."""
    msgs = card.messages()
    assert schema.problems(msgs) == [], schema.problems(msgs)
    assert card.as_text().strip()
    return msgs


# --------------------------------------------------------------------------- datasets


class _B:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime.now(UTC)
        self.n = 0
        self.d: dict[str, Any] = {
            "calls": [],
            "entries": [],
            "runs": [],
            "sessions": [],
            "callbacks": [],
            "messages": {},
            "transcripts": {},
            "allowlist": [],
            "sms_settings": {"invite_cooldown_enabled": True},
        }

    def at(self, hours: float) -> str:
        return (self.now + timedelta(hours=hours)).isoformat()

    def in_hours(self, hours: float) -> str:
        """``hours`` from now, moved to the next quarter hour inside calling hours."""
        t = self.now + timedelta(hours=hours)
        t = t.replace(second=0, microsecond=0)
        t += timedelta(minutes=(15 - t.minute % 15) % 15)
        for _ in range(14 * 24 * 4):
            local = t.astimezone(CLINIC_TZ)
            if local.weekday() <= 5 and 9 <= local.hour < 17:
                return t.isoformat()
            t += timedelta(minutes=15)
        return t.isoformat()

    def phone(self) -> str:
        self.n += 1
        return f"+1202555{100 + self.n % 100:04d}"

    def call(
        self, name, pid, op, ended_h, mins=4, phone=None, op_days_ago=2, diabetic=False, **over
    ):
        ended = self.now + timedelta(hours=ended_h)
        started = ended - timedelta(minutes=mins)
        sid = over.pop("sid", None) or "CA" + re.sub(r"[^0-9a-f]", "a", pid.lower() + "0" * 32)[:32]
        c = {
            "doc_id": sid,
            "call_sid": sid,
            "to": phone or self.phone(),
            "initiated_at": started.isoformat(),
            "updated_at": ended.isoformat(),
            "answered_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_seconds": round(mins * 60),
            "patient_id": pid,
            "patient_name": name,
            # The API never serves the DOB on a call doc.
            "patient_context": {
                "patient_name": name,
                "patient_id": pid,
                "operation_name": op,
                "operation_date": (self.now - timedelta(days=op_days_ago)).date().isoformat(),
                "is_diabetic": diabetic,
            },
        }
        c.update(over)
        self.d["calls"].append(c)
        return c

    def entry(self, name, pid, op, attempts=0, status="pending", phone=None, **over):
        eid = re.sub(r"[^0-9a-f]", "a", pid.lower() + "0" * 32)[:32]
        e = {
            "entry_id": eid,
            "phone": phone or self.phone(),
            "patient_name": name,
            "patient_id": pid,
            "patient_dob": "1958-04-12",
            "operation_name": op,
            "operation_date": (self.now - timedelta(days=2)).date().isoformat(),
            "is_diabetic": False,
            "list_status": status,
            "attempts": attempts,
            "created_at": self.at(-30),
            "updated_at": self.at(-1),
        }
        e.update(over)
        self.d["entries"].append(e)
        return e

    def run(self, when, entries, label=None, status="scheduled", **over):
        r = {
            "run_id": f"{len(self.d['runs']) + 1:032d}",
            "scheduled_for": when,
            "entry_ids": [e["entry_id"] for e in entries],
            "label": label,
            "status": status,
        }
        r.update(over)
        self.d["runs"].append(r)
        return r

    def allow_all_phones(self) -> None:
        phones = {c["to"] for c in self.d["calls"]} | {e["phone"] for e in self.d["entries"]}
        self.d["allowlist"] = [
            {"phone": p, "label": "fixture", "source": "firestore", "editable": True}
            for p in sorted(phones)
        ]


def nurse_day(now=None) -> dict:
    """A realistic day: every outcome, every tier, runs, a cancellation, texts."""
    b = _B(now)
    whitfield = b.call(
        "Dolores Whitfield",
        "P-2041",
        "Total knee replacement",
        -1.2,
        7,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="emergency",
        follow_up_reason="Chest tightness and shortness of breath since this morning; "
        "advised to call 911.",
        dob_verification_result="verified",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 9,
            "pain_trend": "worse",
            "pain_location": "chest",
            "fever_reported": "no",
            "incision_concerns": "no",
            "overall_feeling": "very poor",
        },
    )
    b.d["transcripts"][whitfield["doc_id"]] = {
        "available": True,
        "turns": [
            {"role": "agent", "text": "Hi, this is the automated post-op check-in."},
            {"role": "patient", "text": "My chest feels tight and I'm short of breath."},
            {"role": "agent", "text": "Please hang up and call 911 now."},
        ],
    }
    b.call(
        "Marcus Ellery",
        "P-2042",
        "Laparoscopic cholecystectomy",
        -3.5,
        6,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="urgent_same_day",
        follow_up_reason="Redness and warmth spreading around the incision; "
        "low-grade fever (100.6°F).",
        dob_verification_result="verified",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 5,
            "pain_trend": "same",
            "fever_reported": "yes",
            "incision_concerns": "yes",
            "incision_description": "red, warm, spreading",
            "overall_feeling": "fair",
        },
    )
    b.call(
        "Priya Raman",
        "P-2043",
        "Rotator cuff repair",
        -27,
        5,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="urgent_same_day",
        follow_up_reason="Pain uncontrolled on current regimen; out of prescribed medication.",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 8,
            "pain_trend": "same",
            "fever_reported": "no",
            "incision_concerns": "no",
            "overall_feeling": "poor",
        },
    )
    b.call(
        "Harold Okafor",
        "P-2044",
        "Hip arthroplasty",
        -5,
        6,
        diabetic=True,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="routine_next_business_day",
        follow_up_reason="Blood sugar readings running high (210-240); diabetic.",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 3,
            "pain_trend": "better",
            "fever_reported": "no",
            "incision_concerns": "no",
            "blood_sugar_status": "high",
            "overall_feeling": "good",
        },
    )
    b.call(
        "Lucia Fernández",
        "P-2045",
        "Spinal fusion",
        -2,
        5,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="routine_next_business_day",
        follow_up_reason="Asked about resuming physical therapy and driving.",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 4,
            "pain_trend": "better",
            "fever_reported": "no",
            "incision_concerns": "no",
            "overall_feeling": "good",
        },
    )
    b.call(
        "Gregory Tan",
        "P-2046",
        "Hernia repair",
        -0.6,
        4,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="none",
        follow_up_reason="All answers within expected range.",
        survey_responses={
            "pain_present": "no",
            "fever_reported": "no",
            "incision_concerns": "no",
            "overall_feeling": "great",
        },
    )
    brooks = b.call(
        "Angela Brooks",
        "P-2047",
        "Total knee replacement",
        -4.2,
        0,
        channel="sms",
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="none",
        follow_up_reason="Completed by text; all answers within expected range.",
        survey_responses={
            "pain_present": "yes",
            "pain_severity": 2,
            "pain_trend": "better",
            "fever_reported": "no",
            "incision_concerns": "no",
            "overall_feeling": "good",
        },
    )
    reyes = b.call(
        "Samuel Reyes",
        "P-2048",
        "Carpal tunnel release",
        -6,
        1,
        call_status="left_vmail",
        outcome="voicemail_left",
        follow_up="none",
        follow_up_reason="Voicemail left; no patient contact.",
    )
    castillo = b.call("Naomi Castillo", "P-2049", "Appendectomy", -2.6, 0.5, call_status="rna")
    b.call("Walter Kim", "P-2050", "Cataract surgery", -8, 0, call_status="busy")
    b.call("Beatrice Lowell", "P-2051", "Hip arthroplasty", -1.8, 2, call_status="interrupted")
    b.call(
        "Theo Nakamura",
        "P-2052",
        "ACL reconstruction",
        -3,
        2,
        call_status="success",
        outcome="reschedule_requested",
        follow_up_reason="At work; asked to be called back after 6 pm.",
    )

    b.d["sessions"] = [
        {
            "phone": reyes["to"],
            "call_sid": reyes["call_sid"],
            "patient_name": reyes["patient_name"],
            "patient_id": reyes["patient_id"],
            "state": "scheduled",
            "scheduled_for_text": "later today",
            "last_invite_at": b.at(-5.9),
            "last_inbound_at": b.at(-5.5),
            "message_count": 4,
            "updated_at": b.at(-5.5),
        },
        {
            "phone": castillo["to"],
            "call_sid": castillo["call_sid"],
            "patient_name": castillo["patient_name"],
            "patient_id": castillo["patient_id"],
            "state": "awaiting_choice",
            "last_invite_at": b.at(-2.5),
            "message_count": 1,
            "updated_at": b.at(-2.5),
        },
        {
            "phone": brooks["to"],
            "call_sid": brooks["call_sid"],
            "patient_name": brooks["patient_name"],
            "patient_id": brooks["patient_id"],
            "state": "closed",
            "last_invite_at": b.at(-5),
            "last_inbound_at": b.at(-4.2),
            "message_count": 12,
            "updated_at": b.at(-4.2),
        },
    ]
    b.d["callbacks"] = [
        {
            "callback_id": "cb-1",
            "phone": reyes["to"],
            "run_at": b.in_hours(2.5),
            "status": "scheduled",
            "patient_name": reyes["patient_name"],
            "patient_id": reyes["patient_id"],
        },
    ]
    b.d["messages"][brooks["call_sid"]] = [
        {
            "direction": "outbound",
            "at": b.at(-5),
            "body": "Sorry we missed you. Reply 3 to answer by text.",
        },
        {"direction": "inbound", "at": b.at(-4.9), "body": "3"},
        {"direction": "outbound", "at": b.at(-4.9), "body": "Any pain since your surgery?"},
        {"direction": "inbound", "at": b.at(-4.8), "body": "a little, 2/10, getting better"},
    ]

    jamal = b.entry("Jamal Whitaker", "P-2061", "Laminectomy", attempts=1, status="calling")
    eleanor = b.entry("Eleanor Vance", "P-2060", "Total knee replacement")
    ingrid = b.entry("Ingrid Solberg", "P-2062", "Mastectomy")
    abigail = b.entry("Abigail Porter", "P-2065", "Tonsillectomy")
    naomi = b.entry("Naomi Castillo", "P-2049", "Appendectomy", attempts=1, phone=castillo["to"])
    walter = b.entry("Walter Kim", "P-2050", "Cataract surgery", attempts=1)
    felix = b.entry("Felix Moreau", "P-2063", "Bunionectomy")
    chen = b.entry("Chen Wei", "P-2064", "Sinus surgery")
    b.entry("Odette Marsh", "P-2066", "Shoulder arthroscopy")  # in no run
    b.entry(
        "Desmond Hale",
        "P-2067",
        "Inguinal hernia repair",
        status="canceled",
        canceled={
            "at": b.at(-2.2),
            "reason": "Readmitted overnight for observation; hold calls.",
            "by": "charge.rn@example.org",
        },
    )
    b.run(b.at(-0.05), [jamal], "Post-op day 3", status="dispatched")
    b.run(b.in_hours(0.75), [eleanor, ingrid, abigail], "Post-op day 2")
    b.run(b.in_hours(3.5), [naomi], "Retry", auto_recall=True)
    b.run(b.in_hours(20), [walter, felix], "Next morning")
    b.run(b.in_hours(44), [chen])
    b.allow_all_phones()
    return b.d


def two_anns(now=None) -> dict:
    b = _B(now)
    b.call(
        "Ann Lee",
        "P-4001",
        "Hip arthroplasty",
        -3,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="routine_next_business_day",
        follow_up_reason="Constipation since discharge.",
    )
    b.call(
        "Ann Kim",
        "P-4002",
        "Knee replacement",
        -2,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="none",
        follow_up_reason="All answers within expected range.",
    )
    b.call(
        "Joanne Lee",
        "P-4003",
        "Spinal fusion",
        -1,
        call_status="left_vmail",
        outcome="voicemail_left",
    )
    b.allow_all_phones()
    return b.d


def many_calls(now=None, n: int = 600) -> dict:
    """An urgent patient older than the newest 500 calls (review A-1)."""
    b = _B(now)
    b.call(
        "Old Urgent",
        "P-5000",
        "Hip arthroplasty",
        -400,
        call_status="success",
        outcome="questionnaire_completed",
        follow_up="urgent_same_day",
        follow_up_reason="Fever 101.4 and wound drainage.",
    )
    for i in range(n):
        b.call(
            f"Routine Patient {i:03d}",
            f"P-6{i:03d}",
            "Cataract surgery",
            -300 + i * 0.25,
            call_status="success",
            outcome="questionnaire_completed",
            follow_up="none",
            follow_up_reason="All answers within expected range.",
        )
    return b.d


DATASETS = {"nurse_day": nurse_day, "two_anns": two_anns, "many_calls": many_calls}


# --------------------------------------------------------------------------- fake API client


class FakeBackend:
    """Duck-types voa_nurse.backend.Backend over an in-memory dataset; records writes."""

    CALLS_MAX = 500

    def __init__(self, dataset: str = "nurse_day"):
        self.use(dataset)

    def use(self, name: str) -> None:
        self.state = DATASETS[name]()
        for c in self.state["calls"]:
            assert re.fullmatch(r"\+12025550[01]\d\d", c["to"]), "fictional numbers only"
        self.writes: list[tuple] = []
        self.fail: set[str] = set()

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise BackendError("503: down")

    async def aclose(self) -> None:
        return None

    async def list_calls(self, limit: int = CALLS_MAX, status: str | None = None):
        self._maybe_fail("list_calls")
        self.list_calls_status = status
        calls = [c for c in self.state["calls"] if not status or c.get("call_status") == status]
        calls = sorted(calls, key=lambda c: c.get("initiated_at") or "", reverse=True)
        return calls[:limit], len(calls) >= limit

    async def get_call(self, call_id):
        self._maybe_fail("get_call")
        for c in self.state["calls"]:
            if c["doc_id"] == call_id:
                return c
        raise BackendError("404: Call not found.")

    async def get_transcript(self, call_id):
        return self.state["transcripts"].get(
            call_id,
            {"turns": [], "available": False, "reason": "No transcript recorded for this call."},
        )

    async def place_call(self, body):
        self.writes.append(("place_call", body))
        return {"call_sid": "CA" + uuid.uuid4().hex, "status": "queued"}

    async def set_follow_up(self, call_id, tier, reason):
        self.writes.append(("tier", call_id, tier, reason))
        return {}

    async def get_call_list(self):
        self._maybe_fail("get_call_list")
        return self.state["entries"]

    async def bulk_add_entries(self, entries):
        self.writes.append(("add_entries", entries))
        return {"count": len(entries)}

    async def delete_entry(self, entry_id):
        self.writes.append(("delete_entry", entry_id))
        return {"deleted": entry_id}

    async def get_run(self):
        return {"active": False, "run": None}

    async def start_run(self, entry_ids):
        self.writes.append(("call_now", entry_ids))
        return {"active": True, "run": {}}

    async def cancel_entries(self, entry_ids, reason):
        self.writes.append(("cancel", entry_ids, reason))
        return {"results": [{"entry_id": i, "status": "canceled"} for i in entry_ids]}

    async def reschedule_entry(self, entry_id, run_id=None, scheduled_for=None):
        self.writes.append(("reschedule", entry_id, run_id, scheduled_for))
        when = next(
            (r["scheduled_for"] for r in self.state["runs"] if r["run_id"] == run_id), scheduled_for
        )
        return {"entry_id": entry_id, "run_id": run_id or "new", "scheduled_for": when}

    async def get_call_runs(self):
        self._maybe_fail("get_call_runs")
        return self.state["runs"]

    async def get_call_settings(self):
        return {
            "recall": {"window_start": "09:00", "window_end": "17:00", "days": [0, 1, 2, 3, 4, 5]},
            "timezone": "America/New_York",
        }

    async def get_allowlist(self):
        return {"mode": "list", "unrestricted": False, "entries": self.state["allowlist"]}

    async def add_allowlist(self, phone, label):
        self.writes.append(("allow_add", phone, label))
        return {}

    async def remove_allowlist(self, phone):
        self.writes.append(("allow_remove", phone))
        return {}

    async def get_sessions(self):
        self._maybe_fail("get_sessions")
        return self.state["sessions"]

    async def get_callbacks(self):
        self._maybe_fail("get_callbacks")
        return self.state["callbacks"]

    async def cancel_callback(self, callback_id):
        self.writes.append(("cancel_callback", callback_id))
        return {}

    async def get_messages(self, phone, call_sid=None):
        return {"messages": self.state["messages"].get(call_sid, [])}

    async def get_sms_settings(self):
        return self.state["sms_settings"]

    async def set_sms_setting(self, name, value):
        self.writes.append(("sms_setting", name, value))
        return {name: value}


def make_turn(backend=None, store=None, conversation="conv-1", **kw) -> turn.Turn:
    store = store or MemoryStore()
    return turn.Turn(
        conversation=conversation,
        proposals=ProposalStore(store, conversation),
        backend=backend or FakeBackend(),
        **kw,
    )


def configure_cards() -> None:
    cards.configure(timezone="America/New_York", dashboard_url="https://dashboard.example.org")


# --------------------------------------------------------------------------- model + A2A


class ScriptedLlm(BaseLlm):
    """A model that replays scripted responses and records every request."""

    model: str = "scripted"
    script: list[Any] = Field(default_factory=list)
    requests: list[Any] = Field(default_factory=list)
    delay_s: float = 0.0

    async def generate_content_async(self, llm_request, stream: bool = False):
        import asyncio

        self.requests.append(llm_request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        yield self.script.pop(0) if self.script else text_response("(no more script)")


def call_response(name: str, **args) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        )
    )


def text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


class RecordingQueue(EventQueue):
    def __init__(self):
        super().__init__()
        self.events: list[Any] = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


def request(
    *parts: Part,
    context_id: str = "conv-1",
    a2ui: bool = True,
    headers: dict[str, str] | None = None,
) -> RequestContext:
    msg = Message(role=Role.user, message_id=uuid.uuid4().hex, parts=list(parts))
    call_ctx = ServerCallContext(
        state={"headers": headers or {}}, requested_extensions={EXT_V09} if a2ui else set()
    )
    return RequestContext(
        request=MessageSendParams(message=msg),
        task_id=uuid.uuid4().hex,
        context_id=context_id,
        call_context=call_ctx,
    )


def say(text: str, **kw) -> RequestContext:
    return request(Part(root=TextPart(text=text)), **kw)


def click(name: str, context_id: str = "conv-1", **ctx) -> RequestContext:
    return request(
        Part(root=TextPart(text="User action triggered.")),
        Part(
            root=DataPart(
                data={
                    "action": {
                        "name": name,
                        "context": ctx,
                        "surfaceId": "s",
                        "sourceComponentId": "c",
                    }
                }
            )
        ),
        context_id=context_id,
    )


def reply_parts(q: RecordingQueue) -> list:
    """The parts of the single artifact this turn produced."""
    from a2a.types import TaskArtifactUpdateEvent

    arts = [e for e in q.events if isinstance(e, TaskArtifactUpdateEvent)]
    assert len(arts) == 1, f"expected one artifact, got {len(arts)}"
    return arts[0].artifact.parts


def reply_text(q: RecordingQueue) -> str:
    """The reply rendered as text, whichever form it took (A2UI or text)."""
    out = []
    for p in reply_parts(q):
        root = p.root
        if isinstance(root, TextPart):
            out.append(root.text)
        elif isinstance(root, DataPart) and "updateComponents" in root.data:
            comps = root.data["updateComponents"]["components"]
            out += [c["text"] for c in comps if c.get("component") == "Text"]
    return "\n".join(out)


def buttons(q: RecordingQueue) -> list[dict]:
    """Every button in the reply: {label, event, context}."""
    out = []
    for p in reply_parts(q):
        root = p.root
        if isinstance(root, DataPart) and "updateComponents" in root.data:
            comps = {c["id"]: c for c in root.data["updateComponents"]["components"]}
            for c in comps.values():
                if c.get("component") == "Button" and "event" in c["action"]:
                    ev = c["action"]["event"]
                    out.append(
                        {
                            "label": comps[c["child"]]["text"],
                            "event": ev["name"],
                            "context": ev["context"],
                        }
                    )
    return out
