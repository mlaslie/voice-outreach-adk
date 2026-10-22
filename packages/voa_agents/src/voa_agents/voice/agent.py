"""The outbound voice agent (ADK live agent on Gemini Live)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from importlib import resources
from pathlib import Path

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.genai import types
from voa_core.campaign import DEFAULT_CAMPAIGN_ID, load_campaign
from voa_core.config import AppConfig

from .instruction import build_instruction
from .knowledge import KnowledgeToolset
from .tools import build_tools

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

AGENT_NAME = "Sara"


class PerCallSpeechGemini(Gemini):
    """``Gemini`` whose speech config can be set per call.

    ADK applies ``RunConfig.speech_config`` to the live request, then
    ``Gemini.connect`` overwrites it with the model's own ``speech_config`` (set
    once, at agent build, from ``cfg.voice``). The language is per call
    (docs/LANGUAGES-PLAN.md §3.4), so a call that sets a speech config in its
    ``RunConfig`` (the gateway does, for a non-English call only) keeps it. An
    English call passes none and connects exactly as before."""

    @contextlib.asynccontextmanager
    async def connect(self, llm_request) -> AsyncIterator:
        per_call = llm_request.live_connect_config.speech_config
        if per_call is None:
            async with super().connect(llm_request) as conn:
                yield conn
            return
        model = self.model_copy(update={"speech_config": per_call})
        async with Gemini.connect(model, llm_request) as conn:
            yield conn


def _load_prompt(name: str) -> str:
    return resources.files(__package__).joinpath("prompts", name).read_text()


def build_voice_agent(
    cfg: AppConfig,
    prompt_file: str | None = None,
    *,
    skills: list[str] | None = None,
) -> Agent:
    """Build the live voice agent.

    By default the instruction is generated per session from the campaign named in
    ``state["campaign_id"]`` (see ``instruction.py``). ``prompt_file`` selects a fixed
    template from ``prompts/`` instead (used by the phase-0 spike).

    ``skills``: skill folders exposed through ADK's SkillToolset (loaded on demand by
    the model). Default None = everything is compiled into the instruction.

    The DOB lives in session state but is never rendered: instructions are built with
    explicit fields, not ADK's ``{state}`` templating.
    """
    template = _load_prompt(prompt_file) if prompt_file else None

    def instruction(ctx: ReadonlyContext) -> str:
        patient = ctx.state.get("patient", {})
        if template is None:
            campaign = load_campaign(ctx.state.get("campaign_id") or DEFAULT_CAMPAIGN_ID)
            # The admin console's company name, resolved by the gateway when the call starts.
            org = ctx.state.get("org_name") or cfg.site.org_name
            language = ctx.state.get("call_language")
            if language:
                # A non-English call (the gateway decided it from an approved pack):
                # the pack's wording, plus the language block. English: untouched.
                from voa_core import language_packs

                source = ctx.state.get("call_language_source")
                return build_instruction(
                    language_packs.localized_campaign(campaign, language, source=source),
                    org_name=org,
                    patient=patient,
                    knowledge=None,
                    language=language,
                    language_source=source,
                )
            return build_instruction(
                campaign, org_name=org, patient=patient, knowledge=ctx.state.get("knowledge")
            )
        return template.format(
            agent_name=AGENT_NAME,
            org_name=ctx.state.get("org_name") or cfg.site.org_name,
            patient_first_name=patient.get("first_name", ""),
            patient_last_name=patient.get("last_name", ""),
        )

    model = PerCallSpeechGemini(
        model=cfg.models.live,
        client_kwargs={
            "enterprise": True,  # Vertex (Agent Platform), not the AI Studio API
            "project": cfg.gcp.project_id,
            "location": cfg.models.live_location,  # Live is regional-only
        },
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice.voice_name)
            ),
            language_code=cfg.voice.language_code,
        ),
    )
    return Agent(
        # One agent serves every use case (the instruction is built per call from
        # state["campaign_id"]); the name stays as it was, since ADK session and
        # trace names key on it.
        name="post_op_voice_agent",
        model=model,
        instruction=instruction,
        # The knowledge toolset is empty unless the gateway switched it on for this
        # session (state["knowledge"]); with it off the tools are exactly as before.
        tools=[*build_tools(), KnowledgeToolset(), *_skill_toolset(skills)],
    )


def _skill_toolset(names: list[str] | None) -> list:
    if not names:
        return []
    from google.adk.skills import load_skill_from_dir
    from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset

    toolset = SkillToolset(
        skills=[load_skill_from_dir(SKILLS_DIR / n) for n in names],
        discovery_mode=SkillDiscoveryMode.EAGER,  # small catalog: skip the list_skills turn
    )
    return [toolset]
