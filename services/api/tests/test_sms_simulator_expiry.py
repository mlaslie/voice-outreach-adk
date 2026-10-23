"""Simulator test data expires: every ``simulator__*`` document carries
``expire_at`` (a datetime: Firestore stores it as the Timestamp its TTL policies
need), and Terraform has a TTL policy for every collection the simulator writes.
Fictional data only."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from test_sms_simulator import (
    NOW,
    NS,
    answer_current,
    make,
    reply,
    run,
    sim,
    sim_session,
    start,
)
from voa_api import sms_simulator
from voa_api.context import Api
from voa_core.config import load_config
from voa_core.sms import states as st
from voa_core.store import MemoryStore

SERVICES_TF = Path(__file__).resolve().parents[3] / "infra" / "envs" / "demo" / "services.tf"
EXPIRES = NOW + timedelta(days=7)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def view(clock=None, ttl=timedelta(days=7)):
    store = MemoryStore()
    return store, sms_simulator.SimulatorStore(store, clock=clock or Clock(NOW), ttl=ttl)


def raw(store, coll, doc_id):
    return run(store.get(NS + coll, doc_id))


# --------------------------------------------------------------------------- the store


def test_every_kind_of_write_stamps_expire_at():
    store, v = view()
    run(v.set("sms_sessions", "a", {"state": "x"}))
    run(v.merge("sms_messages", "b", {"body": "hi"}))
    assert run(v.create("sms_consents", "c", {"source": "sms_simulator"})) is True
    run(v.transact("calls", "d", lambda cur: {**(cur or {}), "n": 1}))
    for coll, doc_id in [
        ("sms_sessions", "a"),
        ("sms_messages", "b"),
        ("sms_consents", "c"),
        ("calls", "d"),
    ]:
        assert raw(store, coll, doc_id)["expire_at"] == EXPIRES, coll


def test_each_write_pushes_the_expiry_out():
    clock = Clock(NOW)
    store, v = view(clock)
    run(v.set("sms_sessions", "a", {"state": "x"}))
    clock.at = NOW + timedelta(days=3)
    run(v.merge("sms_sessions", "a", {"state": "y"}))
    assert raw(store, "sms_sessions", "a")["expire_at"] == NOW + timedelta(days=10)
    clock.at = NOW + timedelta(days=4)
    run(v.transact("sms_sessions", "a", lambda cur: {**cur, "state": "z"}))
    assert raw(store, "sms_sessions", "a")["expire_at"] == NOW + timedelta(days=11)


def test_a_transaction_that_writes_nothing_stamps_nothing():
    store, v = view()
    assert run(v.transact("sms_sessions", "a", lambda cur: None)) is None
    assert raw(store, "sms_sessions", "a") is None
    run(store.set(NS + "sms_sessions", "b", {"state": "old"}))
    run(v.transact("sms_sessions", "b", lambda cur: None))
    assert raw(store, "sms_sessions", "b") == {"state": "old"}


def test_the_caller_dict_is_not_mutated():
    _, v = view()
    data = {"state": "x"}
    run(v.set("sms_sessions", "a", data))
    assert data == {"state": "x"}


def test_the_ttl_is_configurable():
    cfg = load_config(
        environ={
            "VOA_ENV": "test",
            "VOA__GCP__PROJECT_ID": "test-project",
            "VOA__SMS__SIMULATOR_TTL_DAYS": "2",
        }
    )
    api = Api(cfg=cfg, store=MemoryStore(), tasks=None, voice=None, clock=Clock(NOW))
    s = sms_simulator.simulator_api(api)
    assert s.store.ttl == timedelta(days=2)
    run(s.store.set("sms_sessions", "a", {"state": "x"}))
    assert run(api.store.get(NS + "sms_sessions", "a"))["expire_at"] == NOW + timedelta(days=2)
    assert load_config(environ={"VOA_ENV": "test"}).sms.simulator_ttl_days == 7


# --------------------------------------------------------------------------- a full run


def test_a_full_text_run_leaves_only_expiring_docs_in_the_listed_collections():
    client, api = make()
    assert start(client, diabetic=True).status_code == 200
    reply(client, "3")
    reply(client, "03/15/1950")
    asked = [False]
    for _ in range(40):
        if sim_session(api)["state"] != st.STATE_SURVEY:
            break
        assert reply(client, answer_current(api, asked)).status_code == 200
    assert sim_session(api)["state"] == st.STATE_CLOSED
    assert reply(client, "STOP").status_code == 200

    sim_colls = {c: docs for c, docs in api.store._data.items() if c.startswith(NS) and docs}
    assert {NS + c for c in ("sms_sessions", "sms_messages", "calls", "reports")} <= set(sim_colls)
    assert {c.removeprefix(NS) for c in sim_colls} <= set(sms_simulator.WRITTEN_COLLECTIONS)
    for coll, docs in sim_colls.items():
        for doc_id, doc in docs.items():
            assert doc.get("expire_at") == EXPIRES, (coll, doc_id)
    # The real store is not stamped (only the admin audit trail is written there).
    for coll, docs in api.store._data.items():
        if not coll.startswith(NS):
            assert all("expire_at" not in d for d in docs.values()), coll
    # The simulator's real time stays NOW: sim(api) follows the service clock.
    assert sim(api).now() == NOW


# --------------------------------------------------------------------------- Terraform


def _tf_list() -> list[str]:
    text = SERVICES_TF.read_text()
    m = re.search(r"simulator_ttl_collections\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "local.simulator_ttl_collections not found in services.tf"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def test_terraform_has_a_ttl_policy_for_every_simulator_collection():
    assert sorted(_tf_list()) == sorted(sms_simulator.WRITTEN_COLLECTIONS)
    text = SERVICES_TF.read_text()
    block = re.search(r'resource "google_firestore_field" "simulator_ttl" \{(.*?)\n\}', text, re.S)
    assert block, "simulator_ttl resource missing"
    body = block.group(1)
    assert "local.simulator_ttl_collections" in body
    assert '"simulator__${each.value}"' in body
    assert 'field      = "expire_at"' in body and "ttl_config {}" in body


@pytest.mark.parametrize("coll", sms_simulator.WRITTEN_COLLECTIONS)
def test_no_listed_collection_is_read_through(coll):
    assert coll not in sms_simulator.READ_THROUGH
