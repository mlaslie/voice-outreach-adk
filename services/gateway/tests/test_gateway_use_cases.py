"""The gateway runs each call on its own use case (docs/USE-CASES-PLAN.md, step 1):
read from the staging doc, else the call doc, else the default; the voicemail is
the use case's; every report the call files is stamped with it."""

import pytest
from voa_core.config import load_config
from voa_core.store import MemoryStore
from voa_gateway.app import CALL_ID_RE, Gateway
from voa_gateway.persist import StoreCallSink
from voa_gateway.session import CallSession
from voa_providers.audio import Codec
from voa_providers.fake import FakeMediaSession

CALL_ID = "CA" + "b" * 32


def cfg():
    return load_config(
        environ={"VOA__GCP__PROJECT_ID": "test-project", "VOA__SERVICES__STORE": "memory"}
    )


def session(store, **kw):
    sink = StoreCallSink(
        store, recordings_bucket=None, project_id="test-project", call_id_pattern=CALL_ID_RE
    )
    s = CallSession(
        cfg=cfg(),
        runner=None,
        media=FakeMediaSession(codec=Codec.MULAW_8K, call_id=CALL_ID),
        sink=sink,
        record=False,
        **kw,
    )
    s.call_id = CALL_ID
    return s, sink


@pytest.mark.parametrize(
    ("staged", "call_doc", "want"),
    [
        ({"campaign_id": "pre_op", "spec_version": 1}, {}, "pre_op"),
        ({}, {"campaign_id": "pre_op"}, "pre_op"),
        ({"campaign_id": "post_op"}, {"campaign_id": "pre_op"}, "post_op"),  # staging wins
        ({}, {}, "post_op"),
        ({"campaign_id": "retired_one"}, {}, "post_op"),  # unknown: the default, logged
    ],
)
async def test_the_call_runs_its_use_case(staged, call_doc, want):
    store = MemoryStore()
    await store.set("calls", CALL_ID, {"call_sid": CALL_ID, **call_doc})
    s, _ = session(store)
    await s._resolve_campaign(staged)
    assert s.campaign_id == want and s.spec_version == 1
    if staged.get("campaign_id") == "retired_one":
        assert any(e.get("event") == "campaign.unknown" for e in s.timeline.entries)


async def test_an_explicit_campaign_is_kept():
    store = MemoryStore()
    await store.set("calls", CALL_ID, {"call_sid": CALL_ID, "campaign_id": "post_op"})
    s, _ = session(store, campaign_id="pre_op")
    await s._resolve_campaign({})
    assert s.campaign_id == "pre_op"


async def test_reports_are_stamped_with_the_use_case():
    store = MemoryStore()
    await store.set("calls", CALL_ID, {"call_sid": CALL_ID})
    s, sink = session(store)
    sink.remember_patient(CALL_ID, {"patient_id": "PT-7"})
    await s._resolve_campaign({"campaign_id": "pre_op", "spec_version": 1})
    await sink.save_report(
        CALL_ID,
        {
            "outcome": "voicemail_left",
            "follow_up": "none",
            "follow_up_reason": "Voicemail left; no patient contact.",
            "survey_responses": {},
            **s._use_case(),
        },
    )
    ((_, rep),) = await store.query("reports")
    assert rep["campaign_id"] == "pre_op" and rep["spec_version"] == 1
    assert (await store.get("calls", CALL_ID))["campaign_id"] == "pre_op"


def test_the_voicemail_is_the_use_cases():
    gw = Gateway(cfg(), store=MemoryStore())
    post = gw.voicemail_text("Acme Health")
    pre = gw.voicemail_text("Acme Health", "pre_op")
    assert post.startswith("Hello, this is Acme Health calling with a follow-up message.")
    assert pre.startswith("Hello, this is Acme Health calling with a message.")
    assert gw.voicemail_text("Acme Health", "retired_one") == post
