"""The outbound voice agent (ADK live agent on Gemini Live)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.genai import types
from voa_core.campaign import load_campaign
from voa_core.config import AppConfig

from .instruction import build_instruction
from .tools import build_tools

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

AGENT_NAME = "Sara"


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
            campaign = load_campaign(ctx.state.get("campaign_id", "post_op"))
            return build_instruction(campaign, org_name=cfg.site.org_name, patient=patient)
        return template.format(
            agent_name=AGENT_NAME,
            org_name=cfg.site.org_name,
            patient_first_name=patient.get("first_name", ""),
            patient_last_name=patient.get("last_name", ""),
        )

    model = Gemini(
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
        name="post_op_voice_agent",
        model=model,
        instruction=instruction,
        tools=[*build_tools(), *_skill_toolset(skills)],
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
