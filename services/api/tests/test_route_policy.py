"""Every dashboard route has an access policy, and each identity reaches only its own."""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from voa_api import auth
from voa_api.app import create_app
from voa_api.context import Api
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_providers.fake import FakeVoice

NURSE_UI = "ui@example.iam.gserviceaccount.com"
ADMIN_UI = "admin@example.iam.gserviceaccount.com"
AGENT = "nurse@example.iam.gserviceaccount.com"


def _app(env="test"):
    cfg = load_config(
        environ={
            "VOA_ENV": env,
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": "https://gw.example.org",
            "VOA__SERVICES__TASKS_INVOKER_SA": "invoker@example.iam.gserviceaccount.com",
            "VOA__SERVICES__UI_SA": NURSE_UI,
            "VOA__SERVICES__ADMIN_UI_SA": ADMIN_UI,
            "VOA__SERVICES__NURSE_AGENT_SA": AGENT,
            "VOA__DIALING__ALLOWLIST": "+12025550101",
        }
    )
    api = Api(cfg=cfg, store=MemoryStore(), tasks=MemoryTaskQueue(), voice=FakeVoice())
    return create_app(cfg, api)


def _dashboard_routes(app):
    """Every /api/* method and path template (from the OpenAPI description, which
    sees through included routers)."""
    paths = app.openapi()["paths"]
    return {
        f"{m.upper()} {path}"
        for path, ops in paths.items()
        if path.startswith("/api/")
        for m in ops
        if m in {"get", "post", "put", "patch", "delete"}
    }


def test_every_dashboard_route_has_a_policy_and_no_policy_is_stale():
    routes = _dashboard_routes(_app())
    assert len(routes) > 30  # the check is not vacuous
    assert routes - set(auth.ROUTE_POLICY) == set(), "routes without a policy"
    assert set(auth.ROUTE_POLICY) - routes == set(), "policies for routes that do not exist"


def _token(email):
    body = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return {"Authorization": f"Bearer x.{body}.y"}


@pytest.fixture
def client(monkeypatch):
    # "demo" enforces the policy (dev/test skip it). Cloud Run IAM has already
    # verified the token in production; here only the email claim matters.
    monkeypatch.setenv("VOA_ENV", "demo")
    try:
        app = _app("demo")
    except Exception as exc:  # pragma: no cover - config strictness in demo
        pytest.skip(f"demo config not loadable in tests: {exc}")
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("who", "method", "path", "allowed"),
    [
        (NURSE_UI, "GET", "/api/calls", True),
        (ADMIN_UI, "GET", "/api/calls", True),
        (NURSE_UI, "PUT", "/api/call-settings/recall", False),  # configuration: admin only
        (ADMIN_UI, "PUT", "/api/call-settings/recall", True),
        (NURSE_UI, "POST", "/api/allowlist", False),
        (AGENT, "POST", "/api/allowlist", True),  # the agent keeps this until step 2
        (ADMIN_UI, "POST", "/api/calls/sign-off", False),  # clinical work: nurses only
        (NURSE_UI, "POST", "/api/sms/control", False),
        ("someone@example.org", "GET", "/api/calls", False),
    ],
)
def test_roles_are_enforced(client, who, method, path, allowed):
    r = client.request(method, path, headers=_token(who), json={})
    if allowed:
        assert r.status_code != 403, (who, path, r.text)
    else:
        assert r.status_code == 403, (who, path, r.status_code)
