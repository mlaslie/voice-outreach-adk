"""Recording links: minted by the API, honoured by the gateway for 15 minutes."""

import asyncio
import time

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from voa_core import links
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_core.tasks import MemoryTaskQueue
from voa_gateway import app as gw_app
from voa_gateway.app import Gateway, create_app

KEY = "k" * 64
HOST = "gw.example.org"
CID = "CA" + "e" * 32


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VOA_SECRET_SOFTPHONE_TOKEN_KEY", KEY)
    cfg = load_config(
        environ={
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SERVICES__GATEWAY_URL": f"https://{HOST}",
            "VOA__SERVICES__STORE": "memory",
            "VOA__AMD__VM_DROP": "false",
            "VOA__SOFTPHONE__ENABLED": "true",
            "VOA__SOFTPHONE__TOKEN_SECRET": "secret://softphone-token-key",
        }
    )
    store = MemoryStore()
    asyncio.run(store.set("calls", CID, {"recording_uri": f"gs://b/recordings/{CID}.wav"}))

    async def fake_stream(project_id, uri, rng=""):
        return Response(content=b"RIFF....WAVE", media_type="audio/wav")

    monkeypatch.setattr(gw_app, "stream_recording", fake_stream)
    app = create_app(cfg, Gateway(cfg, store=store, tasks=MemoryTaskQueue()))
    with TestClient(app, base_url=f"https://{HOST}") as c:
        yield c


def _path(url):
    return url.split(HOST, 1)[1]


def test_a_fresh_link_plays(client):
    url, _ = links.recording_link(f"https://{HOST}", KEY, CID)
    r = client.get(_path(url))
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_expired_tampered_or_other_call_links_are_refused(client):
    old, _ = links.recording_link(f"https://{HOST}", KEY, CID, now=time.time() - 3600)
    assert client.get(_path(old)).status_code == 403
    url, exp = links.recording_link(f"https://{HOST}", KEY, CID)
    assert client.get(_path(url).replace("sig=", "sig=0")).status_code == 403
    other = _path(url).replace(CID, "CA" + "f" * 32)
    assert client.get(other).status_code == 403
    assert client.get(f"/recording/{CID}").status_code == 403


def test_a_softphone_token_signature_cannot_open_a_recording(client):
    import hashlib
    import hmac

    exp = int(time.time()) + 300
    softphone_sig = hmac.new(KEY.encode(), f"{CID}.{exp}".encode(), hashlib.sha256).hexdigest()
    assert client.get(f"/recording/{CID}?exp={exp}&sig={softphone_sig}").status_code == 403


def test_a_call_without_a_recording_is_404(client):
    url, _ = links.recording_link(f"https://{HOST}", KEY, "CA" + "a" * 32)
    assert client.get(_path(url)).status_code == 404
