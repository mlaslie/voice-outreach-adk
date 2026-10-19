"""Settings that belong to the nurse assistant alone.

Shared configuration (project, model, API URL, store, clinic time zone) comes from
``voa_core.config.AppConfig`` (``config/*.yaml`` + ``VOA__SECTION__KEY`` env vars).
``AppConfig`` forbids unknown keys, so the assistant's own knobs live here, read
from ``VOA_NURSE_*`` environment variables (single underscore: they never collide
with the ``VOA__`` nesting scheme).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NurseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOA_NURSE_", extra="ignore", frozen=True)

    # The deployed https URL of this service. It goes into the agent card, and
    # Gemini Enterprise POSTs to whatever the card says (never leave localhost).
    # Shown in Gemini Enterprise (agent card) and on the home card, so a tester can
    # tell this agent apart from the CX-Agent-Studio one ("Post-op calling assistant").
    display_name: str = "Post-op Calling Agent (ADK)"
    public_url: str = "http://localhost:8080"
    # OAuth client id of the authorization resource configured on the Gemini
    # Enterprise agent. Empty = end-user identity is not resolved.
    oauth_client_id: str = ""
    # Comma-separated emails allowed to use the assistant. Empty = anyone Gemini
    # Enterprise lets through. When set, it gates EVERY turn (reads included).
    allowed_emails: str = ""
    # The nurse dashboard (behind IAP). Recordings and live monitoring link here.
    dashboard_url: str = ""
    patient_population: str = "post-operative surgical patients"
    brand_color: str = "#22873B"
    # Debug card exercising A2UI components ("ui check"). Never embeds patient data.
    enable_ui_check: bool = False

    # Gemini Enterprise abandons a stream that is silent for ~50-70 s.
    turn_budget_s: float = Field(default=45.0, ge=1, le=280)
    heartbeat_s: float = Field(default=15.0, ge=0.1, le=40)
    model_timeout_s: float = Field(default=25.0, ge=2, le=120)
    backend_timeout_s: float = Field(default=12.0, ge=1, le=60)

    proposal_ttl_s: int = Field(default=900, ge=60, le=3600)
    session_ttl_h: int = Field(default=24, ge=1, le=24 * 30)
    max_session_events: int = Field(default=60, ge=10, le=400)

    def allowed(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}
