"""GET /api/campaigns: the use cases a call-list entry may name, for the admin
console's CSV import and patient form (no hard-coded list in the UI). Fictional
data only."""

import json
from pathlib import Path

from test_nurse_console_api import ENTRY, make, run
from voa_core.campaign import campaign_ids


def test_lists_every_spec_with_name_and_status_and_the_default():
    client, _, _ = make()
    body = client.get("/api/campaigns").json()
    assert body["default"] == "post_op"
    ids = [c["id"] for c in body["campaigns"]]
    assert ids == campaign_ids() and {"post_op", "pre_op"} <= set(ids)
    pre = next(c for c in body["campaigns"] if c["id"] == "pre_op")
    assert pre == {"id": "pre_op", "name": "Pre-op readiness call", "status": "draft"}


def test_every_listed_id_is_accepted_by_the_call_list():
    client, _, _ = make()
    for i, c in enumerate(client.get("/api/campaigns").json()["campaigns"]):
        body = {**ENTRY, "patient_id": f"P-C{i}", "campaign_id": c["id"]}
        r = client.post("/api/call-list", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["campaign_id"] == c["id"]


def test_campaigns_route_is_a_read_for_every_console():
    from voa_api import auth

    assert auth.ROUTE_POLICY["GET /api/campaigns"] == auth.READ


def test_ui_sample_campaigns_match_the_endpoint():
    """The UI's sample-data copy (?data=demo has no backend) must not drift."""
    client, _, _ = make()
    sample = Path(__file__).parents[3] / "ui/src/lib/campaigns-sample.json"
    assert json.loads(sample.read_text()) == client.get("/api/campaigns").json()


def test_an_edit_without_campaign_id_keeps_it_and_an_explicit_null_clears_it():
    client, api, _ = make()
    entry_id = client.post("/api/call-list", json={**ENTRY, "campaign_id": "pre_op"}).json()[
        "entry_id"
    ]

    def stored():
        return run(api.store.get("call_list", entry_id))["campaign_id"]

    assert client.patch(f"/api/call-list/{entry_id}", json=ENTRY).status_code == 200
    assert stored() == "pre_op"  # an older form that does not send the field
    r = client.patch(f"/api/call-list/{entry_id}", json={**ENTRY, "campaign_id": None})
    assert r.status_code == 200 and stored() is None  # blank: the default (post-op)
