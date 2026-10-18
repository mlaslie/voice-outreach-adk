"""Per-turn context, carried on a ContextVar.

A turn is one A2A request (one Gemini Enterprise chat message). The executor sets
a :class:`Turn` before anything runs; tools, actions and the backend client read
it. Tools append the cards they build to ``turn.cards`` (the model only ever sees
the compact data a tool returns), and the backend client reads ``turn.user`` for
``X-Acting-User``.

A ContextVar holding a mutable object survives ADK running tools in child tasks:
the child copies the context, which still points at the same Turn.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backend import Backend
    from .cards import Card
    from .proposals import ProposalStore


@dataclass
class Turn:
    conversation: str  # the A2A contextId (stable across a Gemini Enterprise conversation)
    proposals: ProposalStore
    backend: Backend
    user: str | None = None  # the signed-in user's email, when identity is configured
    renders_cards: bool = True  # this client asked for A2UI and text mode is off
    cards: list[Card] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


_turn: contextvars.ContextVar[Turn | None] = contextvars.ContextVar("voa_nurse_turn", default=None)


def current() -> Turn:
    t = _turn.get()
    if t is None:
        raise RuntimeError("no active turn")
    return t


def current_or_none() -> Turn | None:
    return _turn.get()


@contextmanager
def active(t: Turn) -> Iterator[Turn]:
    token = _turn.set(t)
    try:
        yield t
    finally:
        _turn.reset(token)
