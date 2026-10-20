"""The A2A executor: one Gemini Enterprise chat message per call.

Per turn, in order (deterministic routes never reach the model):

  1. A button click (A2UI action DataPart)  -> handled in Python.
  2. A CSV attachment                      -> parsed, then a confirmation card.
  3. "text only" / "cards"                 -> toggles text mode (session state).
  4. "confirm <id>"                        -> runs that one proposal, if it is this
                                              conversation's, unused and unexpired.
     A bare "ok" / "yes" / "confirm" never runs anything (review C-5): it gets a
     note pointing at the Confirm button and the reference.
  5. A greeting, "help", "menu" or nothing -> the home card.
  6. Anything else                         -> the ADK agent (tools build the cards).

Expired and used proposals are purged at the top of every turn.

Streaming shape (a2a-cloud-run-gemini-enterprise skill): ``submitted`` ->
``working`` -> a ``working`` heartbeat every ``heartbeat_s`` while the turn runs
(Gemini Enterprise abandons a stream silent for ~50-70 s) -> ONE artifact with
the reply -> ``completed``. The heartbeat stops before the artifact goes out, and
no Message is ever sent after the first response. A turn is capped at
``turn_budget_s`` (review A-5); a model turn that runs out of time is rolled back
out of the session so the next turn starts clean (review A-3).

Replies are A2UI v0.9 DataParts when the client requested the v0.9 extension and
text mode is off, and plain text otherwise (the Gemini Enterprise mobile app
renders no A2UI and cannot be detected, so the user can ask for "text only").
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from a2a.types import TaskArtifactUpdateEvent as ArtifactEvent
from a2ui.a2a.parts import create_a2ui_part
from google.adk.agents.run_config import RunConfig
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types
from voa_core.logging import bind, safe_extra
from voa_core.store import DocStore

from . import actions, cards, schema, turn
from . import worklists as wl
from .backend import Backend, BackendError
from .identity import IdentityResolver
from .proposals import ProposalStore
from .sessions import DocStoreSessionService
from .settings import NurseSettings

log = logging.getLogger(__name__)

APP_NAME = "voa_nurse"
# Sessions are keyed to the conversation, never to the person: an identity that
# resolves on one turn and not the next must not strand the conversation.
SESSION_USER = "ge_conversation"
EXT_V09 = "https://a2ui.org/a2a-extension/a2ui/v0.9"
EXT_V08 = "https://a2ui.org/a2a-extension/a2ui/v0.8"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_LLM_CALLS = 8

_HELLO = re.compile(
    r"^(hi|hello|hey|hiya|howdy|yo|greetings|good (morning|afternoon|evening|day))"
    r"( there| team| all| everyone| assistant)?\b[\s,.!?]*",
    re.I,
)
_ABOUT = re.compile(
    r"^(start|(main |back to( the)? (main )?)?menu|go back|home|help|what can you do|"
    r"what do you do|how can you help( me)?|how does this work|what can i ask( you)?)\b[\s,.!?]*$",
    re.I,
)
CONFIRM_RE = re.compile(r"^\s*confirm\s+(?:ref(?:erence)?\s*)?#?([0-9a-f]{8})\s*[.!]?\s*$", re.I)
# Acknowledgements that must never execute anything (C-5). They only get a pointer.
BARE_CONFIRM = {
    "ok",
    "okay",
    "y",
    "yes",
    "yep",
    "yeah",
    "sure",
    "confirm",
    "confirmed",
    "go ahead",
    "do it",
    "yes confirm",
    "proceed",
}
TEXT_ON = {"text only", "text mode", "no cards", "mobile"}
TEXT_OFF = {"cards", "card mode", "show cards", "cards on"}
CLICK_NOTES = {
    "view_call": "Opened a call",
    "show_transcript": "Opened a transcript",
    "play_recording": "Played a call recording",
    "call_list": "Opened the worklist",
    "followups": "Opened text follow-ups",
    "allowlist": "Opened approved numbers",
    "confirm": "Confirmed an action",
    "dismiss": "Cancelled a confirmation",
}


def is_greeting(text: str) -> bool:
    t = text.strip()
    rest = _HELLO.sub("", t, count=1)
    if rest != t and (not rest or len(rest.split()) == 1):
        return True
    return bool(_ABOUT.match(rest))


def session_id_for(context_id: str) -> str:
    if SESSION_ID_RE.match(context_id):
        return context_id
    return hashlib.sha256(context_id.encode()).hexdigest()


def read_message(
    message: Message | None,
) -> tuple[dict | None, list[str], list[tuple[str, str, bytes]]]:
    """(click action, user texts, attached files). Drops Gemini Enterprise's file
    sentinels and the "User action triggered." text that accompanies a click, and
    de-duplicates repeated text parts."""
    action, texts, files = None, [], []
    for p in message.parts if message else []:
        root = p.root
        if isinstance(root, DataPart) and isinstance(root.data, dict):
            d = root.data
            if isinstance(d.get("action"), dict):
                action = {
                    "name": d["action"].get("name"),
                    "context": d["action"].get("context") or {},
                }
            elif isinstance(d.get("userAction"), dict):  # v0.8 shape
                ua = d["userAction"]
                ctx = ua.get("context")
                if isinstance(ctx, list):
                    ctx = {
                        kv["key"]: (kv.get("value") or {}).get("literalString", kv.get("value"))
                        if isinstance(kv.get("value"), dict)
                        else kv.get("value")
                        for kv in ctx
                        if isinstance(kv, dict) and "key" in kv
                    }
                action = {"name": ua.get("name"), "context": ctx or {}}
        elif isinstance(root, FilePart) and isinstance(root.file, FileWithBytes):
            try:
                raw = base64.b64decode(root.file.bytes)
            except ValueError:
                continue
            files.append((root.file.name or "file", root.file.mime_type or "", raw))
        elif isinstance(root, TextPart):
            t = root.text.strip()
            if (
                t
                and not t.startswith(("<start_of_user_uploaded_file", "<end_of_user_uploaded_file"))
                and t not in texts
            ):
                texts.append(t)
    if action:
        texts = []
        if not isinstance(action.get("context"), dict):
            action["context"] = {}
    return action, texts, files


@dataclass
class Deps:
    runner: Runner
    sessions: DocStoreSessionService
    store: DocStore
    backend: Backend
    identity: IdentityResolver
    settings: NurseSettings


class NurseExecutor(AgentExecutor):
    def __init__(self, deps: Deps | None = None):
        self.deps = deps  # set at startup (app lifespan) or by tests

    # --- A2A ----------------------------------------------------------------------------

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        d = self.deps
        if d is None:
            raise RuntimeError("executor not initialised")
        task_id, context_id = context.task_id, context.context_id or "default"
        requested = set(context.requested_extensions or [])

        if context.current_task is None:
            await event_queue.enqueue_event(self._status(task_id, context_id, TaskState.submitted))
        await event_queue.enqueue_event(self._status(task_id, context_id, TaskState.working))

        stop = asyncio.Event()
        beat = asyncio.create_task(self._heartbeat(event_queue, task_id, context_id, stop))
        out: list[cards.Card] = []
        renders = False
        with bind(session_id=context_id):
            try:
                async with asyncio.timeout(d.settings.turn_budget_s + 10):
                    out, renders = await self._respond(context, EXT_V09 in requested)
            except TimeoutError:
                log.warning("turn exceeded its budget", extra={"event": "nurse.turn.timeout"})
                out = [
                    cards.result_card(
                        "Still working on that",
                        [
                            "That took longer than it should. If you confirmed an action, "
                            "check the dashboard before trying again."
                        ],
                        ok=False,
                    )
                ]
            except Exception:
                log.exception("turn failed", extra={"event": "nurse.turn.error"})
                out = [
                    cards.error_card(
                        "An unexpected error occurred. Please try again; no action was taken."
                    )
                ]
            finally:
                stop.set()
                beat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beat

            parts = self._parts(out, renders and EXT_V09 in requested)
            if renders and EXT_V09 in requested:
                context.add_activated_extension(EXT_V09)
            await event_queue.enqueue_event(
                ArtifactEvent(
                    task_id=task_id,
                    context_id=context_id,
                    artifact=Artifact(artifact_id=uuid.uuid4().hex, parts=parts),
                    last_chunk=True,
                )
            )
            await event_queue.enqueue_event(
                self._status(task_id, context_id, TaskState.completed, final=True)
            )
            log.info(
                "turn done",
                extra=safe_extra(
                    {
                        "event": "nurse.turn",
                        "a2ui": renders,
                        "cards": len(out),
                        "requested": sorted(requested),
                    }
                ),
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None

    @staticmethod
    def _status(task_id, context_id, state, *, final=False, text: str | None = None):
        message = None
        if text:
            message = Message(
                message_id=uuid.uuid4().hex,
                role=Role.agent,
                parts=[Part(root=TextPart(text=text))],
                task_id=task_id,
                context_id=context_id,
            )
        return TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=state, message=message),
            final=final,
        )

    async def _heartbeat(self, q: EventQueue, task_id, context_id, stop: asyncio.Event) -> None:
        period = self.deps.settings.heartbeat_s
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=period)
                return
            except TimeoutError:
                if stop.is_set():
                    return
                await q.enqueue_event(
                    self._status(task_id, context_id, TaskState.working, text="Still working…")
                )

    @staticmethod
    def _parts(out: list[cards.Card], a2ui: bool) -> list[Part]:
        # Every reply ends with a way back to the home card, unless it is the home card.
        if out and not any(c.is_menu for c in out):
            last = next((c for c in reversed(out) if c.wrap), out[-1])
            last.add_menu_button()
        if not a2ui:
            text = "\n\n".join(c.as_text() for c in out) or "Done."
            return [Part(root=TextPart(text=text))]
        parts: list[Part] = []
        for card in out:
            msgs = card.messages()
            problems = schema.problems(msgs)
            if problems:
                # Never send a card that renders blank: send its text instead.
                log.error(
                    "invalid A2UI card",
                    extra={"event": "nurse.a2ui.invalid", "problems": problems[:5]},
                )
                msgs = cards.note(card.as_text()).messages()
            parts += [create_a2ui_part(m, version="0.9") for m in msgs]
        return parts

    # --- the turn -------------------------------------------------------------------------

    async def _respond(
        self, context: RequestContext, wants_a2ui: bool
    ) -> tuple[list[cards.Card], bool]:
        d = self.deps
        conversation = context.context_id or "default"
        headers = (
            context.call_context.state.get("headers") if context.call_context else None
        ) or {}
        user = await d.identity.resolve(headers)
        await wl.refresh_time_format(d.backend)
        await wl.refresh_brand(d.backend)
        allowed = d.settings.allowed()
        if allowed and (user or "") not in allowed:
            # Review A-4: the allowlist gates every turn, reads included.
            return [
                cards.result_card(
                    "Not authorized",
                    ["Your account is not enabled for this assistant. Ask your administrator."],
                    ok=False,
                )
            ], wants_a2ui

        sid = session_id_for(conversation)
        session = await d.sessions.get_session(
            app_name=APP_NAME, user_id=SESSION_USER, session_id=sid
        )
        if session is None:
            try:
                session = await d.sessions.create_session(
                    app_name=APP_NAME,
                    user_id=SESSION_USER,
                    session_id=sid,
                    state={"text_mode": False},
                )
            except ValueError:  # another instance created it first
                session = await d.sessions.get_session(
                    app_name=APP_NAME, user_id=SESSION_USER, session_id=sid
                )
        text_mode = bool(session.state.get("text_mode"))
        t = turn.Turn(
            conversation=conversation,
            proposals=ProposalStore(d.store, conversation, ttl_s=d.settings.proposal_ttl_s),
            backend=d.backend,
            user=user,
            renders_cards=wants_a2ui and not text_mode,
        )
        with turn.active(t):
            await t.proposals.purge_stale()
            out = await self._route(context.message, session, sid)
        # Text mode may have just changed.
        return out, t.renders_cards

    async def _remember(self, session, user_note: str, model_note: str) -> None:
        """Record a deterministic turn in the history, so "call her again" after a
        click still makes sense to the model."""
        d = self.deps
        inv = f"e-{uuid.uuid4().hex[:12]}"
        for author, role, text in (
            ("user", "user", user_note),
            ("nurse_assistant", "model", model_note),
        ):
            ev = Event(
                author=author,
                invocation_id=inv,
                content=types.Content(role=role, parts=[types.Part(text=text)]),
            )
            await d.sessions.append_event(session, ev)

    async def _set_text_mode(self, sid: str, on: bool) -> None:
        await self.deps.sessions.update_state(
            app_name=APP_NAME, user_id=SESSION_USER, session_id=sid, delta={"text_mode": on}
        )

    async def _route(self, message: Message | None, session, sid: str) -> list[cards.Card]:
        t = turn.current()
        d = self.deps
        action, texts, files = read_message(message)

        if action:
            name, ctx = str(action.get("name") or ""), action.get("context") or {}
            t.trace.append({"name": f"click:{name}", "args": sorted(ctx)})
            log.info(
                "click",
                extra=safe_extra({"event": "nurse.click", "name": name, "keys": sorted(ctx)}),
            )
            if name == "ask":
                return await self._model_turn(session, sid, str(ctx.get("text", ""))[:2000])
            if name == "check_submit" and d.settings.enable_ui_check:
                lines = [f"{k}: {json.dumps(v)[:80]}" for k, v in list(ctx.items())[:10]]
                return [cards.result_card("Inputs received", lines or ["No values."])]
            if name == "noop":
                return [cards.note("Button press received.")]
            try:
                card = await actions.handle_click(name, ctx)
            except BackendError as e:
                card = cards.error_card(str(e))
            except (KeyError, TypeError, ValueError):
                card = cards.error_card("That button is missing its details. Please try again.")
            if card is None:
                return [cards.error_card(f"Unknown button '{name}'.")]
            note = CLICK_NOTES.get(name, f"Pressed {name}")
            await self._remember(
                session, f"[{note}: {json.dumps(ctx, default=str)[:300]}]", "[Shown on a card.]"
            )
            return [card]

        csvs = [f for f in files if f[0].lower().endswith(".csv") or "csv" in f[1]]
        if csvs:
            fname, _, raw = csvs[0]
            try:
                entries = actions.parse_call_list_csv(raw)
            except (ValueError, UnicodeError) as e:
                return [cards.result_card("Unable to read the CSV file", [str(e)], ok=False)]
            card, _ = await actions.propose_add_entries(entries, source=fname[:80])
            await self._remember(
                session,
                f"[Attached a CSV with {len(entries)} rows]",
                "[Showed an add-to-worklist confirmation.]",
            )
            return [card]

        text = " ".join(texts).strip()
        low = text.lower().strip(" .!")
        if low in TEXT_ON:
            await self._set_text_mode(sid, True)
            t.renders_cards = False
            return [
                cards.note(
                    "Text-only mode is on. To confirm an action, type confirm and its "
                    "reference. Say 'cards' to switch back."
                )
            ]
        if low in TEXT_OFF:
            await self._set_text_mode(sid, False)
            t.renders_cards = True  # still only if this client asked for A2UI
            return [cards.note("Cards are back on.")]
        if low in ("ui check", "component check") and d.settings.enable_ui_check:
            return [cards.components_check_card()]

        if m := CONFIRM_RE.match(text):
            card = await actions.execute(m.group(1).lower())
            await self._remember(
                session, f"[Typed confirm {m.group(1).lower()}]", "[Shown on a card.]"
            )
            return [card]
        if low in BARE_CONFIRM:
            pending = await t.proposals.pending()
            if pending:
                refs = ", ".join(f"confirm {p.id}" for p in pending[:3])
                return [
                    cards.note(
                        "Nothing was done. A plain yes or ok never confirms an action: "
                        f"press Confirm on the card, or type {refs}."
                    )
                ]

        if not text or is_greeting(text):
            card, _ = await wl.view_home()
            await self._remember(
                session, f"[User said: {text[:200] or '(nothing)'}]", "[Showed the home card.]"
            )
            return [card]

        return await self._model_turn(session, sid, text[:4000])

    async def _model_turn(self, session, sid: str, text: str) -> list[cards.Card]:
        d = self.deps
        t = turn.current()
        keep = {e.id for e in session.events}
        reply = ""
        failed = None
        try:
            async with asyncio.timeout(d.settings.turn_budget_s):
                async with contextlib.aclosing(
                    d.runner.run_async(
                        user_id=SESSION_USER,
                        session_id=sid,
                        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
                        run_config=RunConfig(max_llm_calls=MAX_LLM_CALLS),
                    )
                ) as agen:
                    async for ev in agen:
                        if ev.is_final_response() and ev.content and ev.content.parts:
                            reply = (
                                "".join(
                                    p.text or ""
                                    for p in ev.content.parts
                                    if p.text and not p.thought
                                ).strip()
                                or reply
                            )
        except TimeoutError:
            failed = "timeout"
        except Exception:
            log.exception("model turn failed", extra={"event": "nurse.model.error"})
            failed = "error"
        if failed:
            dropped = await d.sessions.rollback(
                app_name=APP_NAME, user_id=SESSION_USER, session_id=sid, keep_ids=keep
            )
            log.warning(
                "model turn rolled back",
                extra={"event": "nurse.model.rollback", "reason": failed, "dropped": dropped},
            )
            msg = (
                "That took longer than it should. Nothing was changed. Ask again, or use the "
                "buttons below."
                if failed == "timeout"
                else "Something went wrong answering that. Nothing was changed. Please try again."
            )
            return [
                cards.result_card(
                    "Still working on that" if failed == "timeout" else "Unable to answer",
                    [msg],
                    ok=False,
                )
            ]
        out = list(t.cards)
        if reply:
            out.insert(0, cards.note(reply))
        return out or [
            cards.note("I couldn't find an answer to that. Try rephrasing, or use Main menu.")
        ]
