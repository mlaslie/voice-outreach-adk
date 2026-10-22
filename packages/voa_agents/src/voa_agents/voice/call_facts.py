"""Facts about the live conversation that tools need but the model can't be trusted to report.

The gateway (which sees every transcription event) updates a :class:`CallFacts`
per ADK session; tools read it through :func:`facts_for`. It lives in-process
because the bridge and the tools run in the same event loop for the life of a call.
"""

from __future__ import annotations

from dataclasses import dataclass

_REGISTRY: dict[str, CallFacts] = {}
QUESTION_MARKS = ("?", "\uff1f", "\u061f")


@dataclass
class CallFacts:
    caller_turns: int = 0
    # Agent speech (from output transcription, partials included) since the caller last spoke.
    agent_text_since_caller: str = ""
    # Answering-machine detection still pending: the "caller" may be a voicemail
    # greeting, so only the emergency exit is allowed (speak-first AMD strategy).
    amd_pending: bool = False
    identity_verified: bool = False

    def caller_spoke(self) -> None:
        self.caller_turns += 1
        self.agent_text_since_caller = ""

    def agent_said(self, fragment: str) -> None:
        self.agent_text_since_caller += fragment

    @property
    def unanswered_question(self) -> bool:
        # ASCII "?", full-width "？" (Chinese, Japanese) and the Arabic "؟".
        return any(q in self.agent_text_since_caller for q in QUESTION_MARKS)


def register(session_id: str) -> CallFacts:
    facts = _REGISTRY[session_id] = CallFacts()
    return facts


def unregister(session_id: str) -> None:
    _REGISTRY.pop(session_id, None)


def facts_for(session_id: str) -> CallFacts | None:
    return _REGISTRY.get(session_id)
