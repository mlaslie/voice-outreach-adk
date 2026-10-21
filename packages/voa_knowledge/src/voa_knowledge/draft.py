"""Step 4: the draft (docs/KNOWLEDGE-PLAN.md section 3).

An ADK ``LlmAgent`` named ``knowledge_agent`` with a structured ``output_schema``
{covered, answer, source_ids}, run in-process through a small runner: a separate
service would add a network hop to a turn that already holds a patient on the
line. The instruction lives in ``prompts/draft.md`` (``DRAFT_PROMPT_VERSION``).

It sees the question, the patient context (procedure, days since surgery, care
setting, diabetic; never identifiers) and the assembled material. Nothing it
writes reaches a patient before the grounding check and the output veto.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .assemble import Material
from .classify import load_prompt

log = logging.getLogger(__name__)

DRAFT_PROMPT_VERSION = "draft-v1"
AGENT_NAME = "knowledge_agent"
APP_NAME = "voa_knowledge"


class DraftOut(BaseModel):
    """The drafting agent's structured output."""

    covered: bool = False
    answer: str = Field(default="", max_length=2000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class Draft:
    covered: bool
    answer: str = ""
    source_ids: tuple[str, ...] = field(default_factory=tuple)


#: ``await drafter(question, context, materials) -> Draft``. Raises on any error.
Drafter = Callable[[str, Mapping[str, Any], Sequence[Material]], Awaitable[Draft]]


def build_prompt(question: str, context: Mapping[str, Any], materials: Sequence[Material]) -> str:
    patient = {
        k: context[k]
        for k in ("procedure", "days_since_surgery", "care_setting", "is_diabetic")
        if context.get(k) is not None
    }
    blocks = [
        f"[{m.ref}] ({m.kind}{', ' + m.title if m.kind == 'guideline' else ''})\n{m.text}"
        for m in materials
    ]
    return (
        "Patient (what the clinic knows; may be incomplete):\n"
        f"{json.dumps(patient, ensure_ascii=False)}\n\n"
        "Approved material:\n" + "\n\n".join(blocks) + "\n\n"
        "Patient's question (between the markers):\n"
        f"<<<\n{question[:300]}\n>>>"
    )


def build_agent(model: Any, thinking_budget: int | None = 0):
    """The ADK agent. ``model``: a model name or an ADK ``BaseLlm``.
    ``thinking_budget``: 0 turns thinking off (the in-call budget needs it)."""
    from google.adk.agents import LlmAgent
    from google.genai import types

    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        description="Answers a patient's question only from the clinic's approved material.",
        instruction=load_prompt("draft.md"),
        output_schema=DraftOut,
        include_contents="none",  # one question per run; no history
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(
            temperature=0,
            thinking_config=None
            if thinking_budget is None
            else types.ThinkingConfig(thinking_budget=thinking_budget),
        ),
    )


def vertex_model(cfg) -> Any:
    from google.adk.models import Gemini

    return Gemini(
        model=cfg.models.knowledge_draft,
        client_kwargs={
            "enterprise": True,  # Vertex (Agent Platform), not the AI Studio API
            "project": cfg.gcp.project_id,
            "location": cfg.models.knowledge_location,
        },
    )


class AdkDrafter:
    """``await drafter(question, context, materials) -> Draft`` through an ADK runner
    with an in-memory session per question (deleted afterwards)."""

    def __init__(self, cfg=None, *, model: Any = None):
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self._model = model if model is not None else vertex_model(cfg)
        self.agent = build_agent(
            self._model,
            thinking_budget=cfg.models.knowledge_draft_thinking_budget if cfg is not None else 0,
        )
        self._sessions = InMemorySessionService()
        self._runner = Runner(app_name=APP_NAME, agent=self.agent, session_service=self._sessions)
        self._warmed = False

    async def warm(self) -> None:
        """Sign in and open the model connection ahead of the first question."""
        if self._warmed:
            return
        self._warmed = True
        try:
            client = getattr(self._model, "api_client", None)
            if client is not None:
                await client.aio.models.get(model=self._model.model)
        except Exception:  # noqa: BLE001 - best effort
            self._warmed = False

    async def __call__(
        self, question: str, context: Mapping[str, Any], materials: Sequence[Material]
    ) -> Draft:
        from google.genai import types

        user = f"q-{uuid.uuid4().hex[:12]}"
        session = await self._sessions.create_session(app_name=APP_NAME, user_id=user)
        text = ""
        try:
            async for event in self._runner.run_async(
                user_id=user,
                session_id=session.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=build_prompt(question, context, materials))],
                ),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    text = "".join(p.text or "" for p in event.content.parts if p.text)
        finally:
            await self._sessions.delete_session(
                app_name=APP_NAME, user_id=user, session_id=session.id
            )
        out = DraftOut.model_validate_json(text or "{}")
        return Draft(out.covered, out.answer.strip(), tuple(out.source_ids))
