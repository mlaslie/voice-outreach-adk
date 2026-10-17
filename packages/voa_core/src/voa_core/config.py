"""Layered, validated configuration.

Precedence, lowest to highest:

1. ``config/app.yaml``                      committed, no identifying values
2. ``config/site.yaml``                     gitignored (org name, project id, ...)
3. ``config/providers/<name>.yaml``         gitignored (numbers, account ids)
4. ``config/environments/<VOA_ENV>.yaml``   gitignored per-environment overrides
5. environment variables ``VOA__SECTION__KEY=value`` (double underscore = nesting)

Values of the form ``secret://<name>`` are Secret Manager references. They are
resolved by :func:`resolve_secret` at the point of use, never stored in logs.

Startup fails closed: an invalid or incomplete config raises ``ConfigError``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SECRET_PREFIX = "secret://"  # noqa: S105 - a URI scheme, not a secret


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GcpConfig(_Strict):
    project_id: str = Field(min_length=6)
    region: str = "us-central1"


class ModelsConfig(_Strict):
    live: str = "gemini-3.8-live"
    # Live models are served from regional endpoints only; never "global".
    live_location: str = "us-central1"
    text: str = "gemini-3.8-flash"
    text_location: str = "global"
    tts: str = "gemini-2.5-flash-tts"
    tts_location: str = "us-central1"


class VoiceConfig(_Strict):
    voice_name: str = "Aoede"
    language_code: str = "en-US"
    custom_vocabulary: list[str] = Field(default_factory=list)
    session_resumption: bool = True
    max_call_seconds: int = Field(default=900, ge=60, le=3600)


class SiteConfig(_Strict):
    org_name: str = "Acme Health"
    support_email: str = "support@example.org"
    privacy_url: str = "https://example.org/privacy"
    terms_url: str = "https://example.org/terms"
    timezone: str = "America/New_York"


class TelephonyConfig(_Strict):
    voice_provider: Literal["twilio", "browser", "fake"] = "browser"
    sms_provider: Literal["twilio", "fake"] = "fake"
    public_base_url: str = ""  # e.g. https://voa-gateway-xxxx.run.app (no trailing slash)


class AmdConfig(_Strict):
    """Answering-machine detection: placed with the call, acted on by the gateway."""

    enabled: bool = True
    mode: Literal["Enable", "DetectMessageEnd"] = "DetectMessageEnd"
    timeout_s: int = Field(default=55, ge=3, le=59)
    speech_end_threshold_ms: int = Field(default=1000, ge=500, le=5000)
    hold_max_s: float = Field(default=4.0, ge=0, le=30)
    late_window_s: float = Field(default=65.0, ge=0, le=120)
    vm_drop: bool = True
    vm_drop_verdicts: list[str] = Field(default_factory=lambda: ["machine_end_beep"])


class ServicesConfig(_Strict):
    """Where the pieces live. Filled from Terraform outputs per environment."""

    gateway_url: str = ""  # public, e.g. https://voa-gateway-xxxx.run.app
    api_url: str = ""  # private
    tasks_invoker_sa: str = ""
    ui_sa: str = ""  # the dashboard's runtime SA (allowed to call /api/*)
    nurse_agent_sa: str = ""  # post-v1 nurse assistant
    recordings_bucket: str = ""
    prompts_bucket: str = ""
    store: Literal["firestore", "memory"] = "firestore"
    queue_dial: str = "voa-dial"
    queue_events: str = "voa-events"
    queue_sms: str = "voa-sms"


class DialingConfig(_Strict):
    """Abuse controls and call-list limits (F4/F5). Fail closed by default."""

    # Static allowlist: comma-separated E.164 numbers, prefixes (>= 8 digits) or "*".
    # Union-ed with the Firestore allowlist; empty + empty = nothing is dialable.
    allowlist: str = ""
    max_dials_per_hour: int = Field(default=30, ge=0)
    max_dials_per_run: int = Field(default=50, ge=1)
    max_concurrent_calls: int = Field(default=1, ge=1, le=20)
    max_list_size: int = Field(default=1000, ge=1)
    max_list_writes_per_hour: int = Field(default=600, ge=0)
    call_timeout_s: int = Field(default=600, ge=60)  # a call "in flight" longer than this is stale


class SoftphoneConfig(_Strict):
    """Browser softphone into the gateway. Off in deployed environments unless a
    token secret is configured (tokens are minted by the API behind IAP)."""

    enabled: bool = False
    token_secret: str = ""  # secret:// reference; empty = no token required (local dev only)


class DebugConfig(_Strict):
    enabled: bool = False
    log_tool_args: bool = False  # never true where real PHI flows
    save_audio: bool = False


class AppConfig(_Strict):
    env: str = "dev"
    gcp: GcpConfig
    models: ModelsConfig = ModelsConfig()
    voice: VoiceConfig = VoiceConfig()
    site: SiteConfig = SiteConfig()
    telephony: TelephonyConfig = TelephonyConfig()
    amd: AmdConfig = AmdConfig()
    services: ServicesConfig = ServicesConfig()
    softphone: SoftphoneConfig = SoftphoneConfig()
    dialing: DialingConfig = DialingConfig()
    debug: DebugConfig = DebugConfig()
    # Provider-specific blocks are validated by the provider adapter itself.
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    return data


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    """``VOA__VOICE__VOICE_NAME=Kore`` -> ``{"voice": {"voice_name": "Kore"}}``."""
    out: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith("VOA__"):
            continue
        parts = [p.lower() for p in key[5:].split("__") if p]
        if not parts:
            continue
        # Keep strings as strings and let pydantic coerce ("true" -> bool, "8" -> int).
        # YAML-parsing would turn "+12025550123" into an int. Only explicit JSON
        # lists/objects are parsed.
        value: Any = raw
        if raw and raw[0] in "[{":
            value = json.loads(raw)
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def find_config_dir(start: Path | None = None) -> Path:
    explicit = os.environ.get("VOA_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config" / "app.yaml").is_file():
            return candidate / "config"
    raise ConfigError("config/app.yaml not found; set VOA_CONFIG_DIR")


def load_config(config_dir: Path | None = None, environ: dict[str, str] | None = None) -> AppConfig:
    environ = dict(os.environ if environ is None else environ)
    cdir = config_dir or find_config_dir()
    env_name = environ.get("VOA_ENV", "dev")

    merged: dict[str, Any] = {}
    layers = [cdir / "app.yaml", cdir / "site.yaml"]
    layers += sorted((cdir / "providers").glob("*.yaml")) if (cdir / "providers").is_dir() else []
    layers.append(cdir / "environments" / f"{env_name}.yaml")
    for layer in layers:
        if layer.name.endswith(".example.yaml"):
            continue
        data = _read_yaml(layer)
        if layer.parent.name == "providers" and data:
            data = {"providers": {layer.stem: data}}
        merged = _deep_merge(merged, data)
    merged = _deep_merge(merged, _env_overrides(environ))
    # VOA_ENV is authoritative: a file must never be able to make a deployed
    # service believe it is "dev" (dev relaxes auth checks).
    if "VOA_ENV" in environ:
        merged["env"] = env_name
    else:
        merged.setdefault("env", env_name)

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        # Field names only; never echo values (they may be identifying).
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None


def resolve_secret(value: str, project_id: str) -> str:
    """Resolve ``secret://name[#version]`` via Secret Manager; pass other values through.

    Local development can shadow a secret with ``VOA_SECRET_<NAME>`` (dashes -> underscores).
    """
    if not value.startswith(SECRET_PREFIX):
        return value
    ref = value[len(SECRET_PREFIX) :]
    name, _, version = ref.partition("#")
    shadow = os.environ.get("VOA_SECRET_" + name.upper().replace("-", "_"))
    if shadow:
        return shadow
    from google.cloud import secretmanager  # imported lazily; optional dependency

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{project_id}/secrets/{name}/versions/{version or 'latest'}"
    return client.access_secret_version(name=path).payload.data.decode("utf-8")
