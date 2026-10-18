"""voa-nurse: the nurse assistant, served over A2A for Gemini Enterprise.

Routes:
  GET  /health                         liveness
  GET  /.well-known/agent-card.json    the agent card (A2UI v0.9 + v0.8 extensions declared)
  POST /                               A2A JSON-RPC (message/stream is what Gemini Enterprise uses)

Private Cloud Run service: Gemini Enterprise invokes it as its Discovery Engine
service agent (``roles/run.invoker`` on this service). The signed-in user's
identity arrives separately (see ``identity.py``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2ui.a2a.extension import get_a2ui_agent_extension
from google.adk.runners import Runner
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from voa_core.config import AppConfig, load_config
from voa_core.logging import configure_logging
from voa_core.store import DocStore, FirestoreStore, MemoryStore

from . import cards
from .agent import build_agent
from .backend import Backend
from .executor import APP_NAME, Deps, NurseExecutor
from .identity import IdentityResolver
from .sessions import DocStoreSessionService
from .settings import NurseSettings

log = logging.getLogger("voa_nurse")

A2UI_MIME = "application/json+a2ui"
GE_COMPOSITE = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
)
V08_BASIC = "https://a2ui.org/specification/v0_8/basic_catalog.json"
# a2a-sdk defaults to 10 MB; Gemini Enterprise inlines attachments. 32 MB is
# Cloud Run's HTTP/1 request limit, so more would only move the rejection.
MAX_BODY = 32 * 1024 * 1024


def agent_card(settings: NurseSettings) -> AgentCard:
    return AgentCard(
        name="Post-op Follow-up Assistant",
        description=(
            f"Post-discharge follow-up for {settings.patient_population}: see who needs RN "
            "follow-up and why, review each automated check-in call (symptom review, "
            "disposition, transcript), place follow-up calls and manage the outreach worklist. "
            "Placing a call or changing anything always needs the user's confirmation."
        ),
        # The deployed https URL. Gemini Enterprise stores what the card says at
        # registration and POSTs there; never publish a localhost card.
        url=f"{settings.public_url.rstrip('/')}/",
        version="0.1.0",
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extensions=[
                get_a2ui_agent_extension(
                    version="0.9", supported_catalog_ids=[cards.V09_BASIC, GE_COMPOSITE]
                ),
                # Declared so Gemini Enterprise negotiates v0.9 (it picks the highest
                # declared version); v0.8 is never emitted.
                get_a2ui_agent_extension(version="0.8", supported_catalog_ids=[V08_BASIC]),
            ],
        ),
        default_input_modes=["text/plain", "text/csv"],
        default_output_modes=["text/plain", A2UI_MIME],
        skills=[
            AgentSkill(
                id="review",
                name="Patient review",
                tags=["follow-up", "triage"],
                description="Who needs RN follow-up and why, symptom review and disposition for "
                "each call, today's and previous calls.",
                examples=[
                    "Who needs a callback today?",
                    "Show Jordan Rivera's last call",
                    "Did she report a fever?",
                ],
            ),
            AgentSkill(
                id="outreach",
                name="Outreach calls",
                tags=["calls"],
                description="Place a follow-up call, call a patient back, cancel or reschedule "
                "a scheduled call (always with confirmation).",
                examples=["Call Jordan Rivera back", "Who is going to be called today?"],
            ),
            AgentSkill(
                id="text",
                name="Text message follow-up",
                tags=["sms"],
                description="Text threads and callbacks patients scheduled by text.",
                examples=["Any callbacks scheduled by text?"],
            ),
        ],
    )


def build_deps(
    cfg: AppConfig,
    settings: NurseSettings,
    *,
    store: DocStore | None = None,
    backend: Backend | None = None,
    identity: IdentityResolver | None = None,
    model=None,
) -> Deps:
    cards.configure(
        timezone=cfg.site.timezone, dashboard_url=settings.dashboard_url, brand=settings.brand_color
    )
    if store is None:
        store = (
            MemoryStore() if cfg.services.store == "memory" else FirestoreStore(cfg.gcp.project_id)
        )
    sessions = DocStoreSessionService(
        store, max_events=settings.max_session_events, ttl_hours=settings.session_ttl_h
    )
    runner = Runner(
        app_name=APP_NAME, agent=build_agent(cfg, settings, model=model), session_service=sessions
    )
    return Deps(
        runner=runner,
        sessions=sessions,
        store=store,
        backend=backend or Backend(cfg.services.api_url, timeout_s=settings.backend_timeout_s),
        identity=identity or IdentityResolver(settings.oauth_client_id),
        settings=settings,
    )


def create_app(
    cfg: AppConfig | None = None,
    settings: NurseSettings | None = None,
    *,
    deps: Deps | None = None,
):
    settings = settings or (deps.settings if deps else NurseSettings())
    executor = NurseExecutor(deps)

    @asynccontextmanager
    async def lifespan(_app):
        c = cfg or load_config()
        configure_logging(c.log_level, c.gcp.project_id)
        if executor.deps is None:
            executor.deps = build_deps(c, settings)
        if settings.public_url.startswith("http://localhost") and c.env not in ("dev", "test"):
            log.warning("agent card URL is localhost", extra={"event": "nurse.card.localhost"})
        yield
        await executor.deps.backend.aclose()

    async def health(_request: Request):
        return JSONResponse({"ok": True})

    a2a = A2AStarletteApplication(
        agent_card=agent_card(settings),
        http_handler=DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()),
        max_content_length=MAX_BODY,
    )
    app = a2a.build(routes=[Route("/health", health, methods=["GET"])], lifespan=lifespan)
    app.state.executor = executor
    return app


app = create_app()
