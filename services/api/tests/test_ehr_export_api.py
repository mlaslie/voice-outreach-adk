"""Admin → EHR export API (docs/EHR-EXPORT-PLAN.md §4.2b). Fictional data only;
no FHIR server is contacted (preview is offline, simulate uses the in-memory fake)."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from test_api import make, run
from test_route_policy import ADMIN_UI, NURSE_UI, _app, _token
from voa_api import audit
from voa_ehr import versions as vs

BASE = "/api/ehr-export"
ADMIN = {"X-Acting-User": "admin.one@example.org"}
ROOT = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((ROOT / "config/ehr/samples/post_op.outbox.json").read_text())
EXAMPLE_YAML = (ROOT / "config/ehr/profiles/post_op.example.yaml").read_text()
DOB = "1950-03-15"
CALL_ID = "CA" + "ab" * 16


@pytest.fixture
def setup():
    return make()


def example(client):
    r = client.get(f"{BASE}/example")
    assert r.status_code == 200, r.text
    return r.json()["profile"]


def put_draft(client, profile=None, **body):
    r = client.put(
        f"{BASE}/draft", json={"profile": profile, **body} if profile else body, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    return r.json()


def store_call(api, call_id=CALL_ID, **record_changes):
    rec = copy.deepcopy(SAMPLE["record"])
    rec["call_sid"] = call_id
    rec.update(record_changes)
    call = {**copy.deepcopy(SAMPLE["call"]), "patient_dob": DOB}
    run(api.store.set("ehr_outbox", call_id, rec))
    run(api.store.set("calls", call_id, call))
    return rec


def resolve(doc, ptr):
    cur = doc
    for raw in [p for p in ptr.split("/")[1:]] if ptr else []:
        part = raw.replace("~1", "/").replace("~0", "~")
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def audit_actions(api):
    rows = run(api.store.query(audit.COLLECTION))
    return sorted(d["action"] for _, d in rows)


# --- versions -------------------------------------------------------------------------


def test_the_full_version_cycle(setup):
    client, api = setup
    state = client.get(BASE).json()
    assert state["active"] is None and state["draft"] is None
    assert state["active_version"] is None and state["draft_version"] is None
    assert "post_op" in state["use_cases"]

    profile = example(client)
    v = client.post(f"{BASE}/validate", json={"profile": profile}).json()
    assert not [p for p in v["problems"] if p["level"] == "error"]

    d1 = put_draft(client, profile, note="first")
    assert d1["version"] == 1 and d1["status"] == "draft"
    assert d1["profile"]["version"] == 1 and d1["saved_by"] == "admin.one@example.org"
    assert set(d1) == {
        "version",
        "status",
        "profile",
        "problems",
        "saved_by",
        "saved_at",
        "activated_by",
        "activated_at",
        "note",
    }
    # Saving again replaces the same draft.
    profile["name"] = "Example Health - edited"
    assert put_draft(client, profile)["version"] == 1

    a1 = client.post(f"{BASE}/activate", json={"note": "go live"}, headers=ADMIN).json()
    assert a1["status"] == "active" and a1["activated_by"] == "admin.one@example.org"
    assert a1["note"] == "go live"
    state = client.get(BASE).json()
    assert state["active_version"] == 1 and state["draft_version"] is None
    assert state["active"]["profile"]["name"] == "Example Health - edited"

    # A second version: a new draft number, activated; the first is retired.
    profile["name"] = "Example Health - v2"
    assert put_draft(client, profile)["version"] == 2
    assert client.post(f"{BASE}/activate").json()["version"] == 2
    versions = client.get(f"{BASE}/versions").json()["versions"]
    assert [(x["version"], x["status"]) for x in versions] == [(2, "active"), (1, "retired")]
    assert all("profile" not in x for x in versions)

    # Restore v1: it becomes a new draft (3), with v1's content.
    r = client.post(f"{BASE}/versions/1/restore", headers=ADMIN).json()
    assert r["version"] == 3 and r["status"] == "draft"
    assert r["profile"]["name"] == "Example Health - edited" and r["profile"]["version"] == 3
    assert r["note"] == "restored from version 1"

    # Reset the draft to the active one.
    r = client.post(f"{BASE}/draft/reset").json()
    assert r["version"] == 3 and r["profile"]["name"] == "Example Health - v2"
    assert client.post(f"{BASE}/versions/9/restore").status_code == 404

    # Writes are audited; validate is read-only.
    acts = audit_actions(api)
    assert "PUT /api/ehr-export/draft" in acts
    assert "POST /api/ehr-export/activate" in acts
    assert "POST /api/ehr-export/versions/{n}/restore" in acts
    assert "POST /api/ehr-export/draft/reset" in acts
    assert "POST /api/ehr-export/validate" not in acts
    # The exporter's reader sees the active version.
    active = run(vs.active_profile(api.store))
    assert active is not None and active.version == 2


def test_activate_is_refused_on_errors(setup):
    client, api = setup
    profile = example(client)
    profile["use_cases"][0]["questionnaire_response"]["items"]["not_a_question"] = {
        "link_id": "99",
        "type": "string",
    }
    d = put_draft(client, profile)  # saved anyway: work is not lost
    errors = [p for p in d["problems"] if p["level"] == "error"]
    assert errors and "not_a_question" in errors[0]["path"]
    r = client.post(f"{BASE}/activate")
    assert r.status_code == 409
    assert r.json()["detail"]["problems"] == d["problems"]
    assert client.get(BASE).json()["active_version"] is None
    assert "POST /api/ehr-export/activate" not in audit_actions(api)
    # No draft at all: 409 too.
    client2, _ = make()
    assert client2.post(f"{BASE}/activate").status_code == 409
    assert client2.post(f"{BASE}/draft/reset").status_code == 409


def test_a_draft_that_does_not_parse_is_saved_with_its_problems(setup):
    client, _ = setup
    profile = example(client)
    profile["target"]["base_url"] = "http://fhir.example.org/R4"  # not https
    d = put_draft(client, profile)
    assert any(p["level"] == "error" and "target" in p["path"] for p in d["problems"])
    assert client.post(f"{BASE}/activate").status_code == 409
    # preview with the broken draft: 422 with the problems
    r = client.post(f"{BASE}/preview", json={"source": {"sample": "post_op"}})
    assert r.status_code == 422 and r.json()["detail"]["problems"]


def test_yaml_round_trip(setup):
    client, _ = setup
    d = put_draft(client, yaml=EXAMPLE_YAML)
    assert d["profile"]["id"] == "post_op_example"
    client.post(f"{BASE}/activate")
    r = client.get(f"{BASE}/yaml", params={"version": "active"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/yaml")
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    # Upload what we downloaded: the same profile again.
    d2 = put_draft(client, yaml=text)
    assert d2["version"] == 2
    assert {**d2["profile"], "version": 1} == client.get(BASE).json()["active"]["profile"]
    assert yaml.safe_load(text)["id"] == "post_op_example"
    assert client.get(f"{BASE}/yaml", params={"version": "1"}).status_code == 200
    assert client.get(f"{BASE}/yaml", params={"version": "draft"}).status_code == 200
    assert client.get(f"{BASE}/yaml", params={"version": "7"}).status_code == 404
    assert client.get(f"{BASE}/yaml", params={"version": "x"}).status_code == 422
    # Bad YAML and both forms at once are refused.
    assert client.put(f"{BASE}/draft", json={"yaml": "a: [1"}).status_code == 422
    assert client.put(f"{BASE}/draft", json={"yaml": "- 1\n- 2"}).status_code == 422
    both = {"yaml": EXAMPLE_YAML, "profile": {"id": "x"}}
    assert client.put(f"{BASE}/draft", json=both).status_code == 422
    v = client.post(f"{BASE}/validate", json={"yaml": EXAMPLE_YAML}).json()
    assert not [p for p in v["problems"] if p["level"] == "error"]


def test_size_limits(setup):
    client, _ = setup
    big = "x" * 210_000
    assert client.put(f"{BASE}/draft", json={"yaml": f"name: {big}"}).status_code == 413
    profile = example(client)
    profile["description"] = big
    assert client.put(f"{BASE}/draft", json={"profile": profile}).status_code == 413
    assert client.post(f"{BASE}/validate", json={"profile": profile}).status_code == 413
    # just under the limit is fine
    profile["description"] = "x" * 150_000
    assert client.post(f"{BASE}/validate", json={"profile": profile}).status_code == 200


def test_questions_and_example(setup):
    client, _ = setup
    q = client.get(f"{BASE}/questions", params={"campaign_id": "post_op"}).json()
    assert q["campaign_id"] == "post_op" and q["spec_version"] >= 1
    by = {x["id"]: x for x in q["questions"]}
    assert by["pain_present"]["allowed_types"] == ["boolean", "coding", "string"]
    assert by["pain_present"]["choices"] == ["yes", "no"]
    assert "integer" in by["pain_severity"]["allowed_types"]
    assert by["pain_trend"]["choices"] and by["pain_trend"]["ask"]
    assert by["patient_questions"]["engine"] is True
    assert client.get(f"{BASE}/questions", params={"campaign_id": "nope"}).status_code == 404
    assert client.get(f"{BASE}/questions").status_code == 422
    assert example(client)["id"] == "post_op_example"


# --- preview and simulate -----------------------------------------------------------


def test_preview_sources(setup):
    client, api = setup
    for i in range(25):
        cid = "CA" + f"{i:02d}" * 16
        store_call(api, cid, created_at=f"2026-10-{(i % 28) + 1:02d}T10:00:00.000+00:00")
    s = client.get(f"{BASE}/preview-sources").json()
    (sample,) = s["samples"]
    assert sample["campaign_id"] == "post_op" and sample["label"].startswith("Fictional sample")
    assert len(s["calls"]) == 20
    assert all(c["label"] == "Jordan R." for c in s["calls"])
    assert all(c["campaign_id"] == "post_op" for c in s["calls"])
    assert set(s["calls"][0]) == {"call_id", "campaign_id", "signed_off_at", "status", "label"}
    created = [run(api.store.get("ehr_outbox", c["call_id"]))["created_at"] for c in s["calls"]]
    assert created == sorted(created, reverse=True)
    assert DOB not in json.dumps(s)


def _check_preview(out, record):
    assert set(out) >= {
        "ok",
        "source_record",
        "bundle",
        "source_map",
        "skipped",
        "warnings",
        "errors",
    }
    assert out["source_map"]
    for row in out["source_map"]:
        resolve(out["bundle"], row["target"])
        if row["source"] is not None:
            resolve(out["source_record"], row["source"])
    for s in out["skipped"]:
        assert set(s) == {"question_id", "reason", "source"}
        resolve(out["source_record"], s["source"])
    assert out["source_record"]["questionnaire_response"] == record["questionnaire_response"]


def test_preview_with_the_sample_falls_back_to_the_example(setup):
    client, api = setup
    r = client.post(f"{BASE}/preview", json={"source": {"sample": "post_op"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["profile_source"] == "example"
    assert len(out["bundle"]["entry"]) == 4
    _check_preview(out, SAMPLE["record"])
    assert out["bundle"]["entry"][0]["resource"]["subject"] == {
        "reference": "Patient/example-patient"
    }
    assert client.post(f"{BASE}/preview", json={"source": {"sample": "nope"}}).status_code == 404
    bad = {"source": {"sample": "post_op", "call_id": CALL_ID}}
    assert client.post(f"{BASE}/preview", json=bad).status_code == 422
    assert audit_actions(api) == []  # read-only


def test_preview_with_a_stored_call_and_the_draft(setup):
    client, api = setup
    profile = example(client)
    del profile["use_cases"][0]["questionnaire_response"]["items"]["sleeping_well"]
    put_draft(client, profile)
    rec = store_call(api, patient={"mrn": "A1234567", "name": "Jordan Rivera", "dob": DOB})
    out = client.post(f"{BASE}/preview", json={"source": {"call_id": CALL_ID}}).json()
    assert out["profile_source"] == "draft"
    _check_preview(out, rec)
    (s,) = [s for s in out["skipped"] if s["question_id"] == "sleeping_well"]
    assert s["reason"] == "not mapped"
    assert resolve(out["source_record"], s["source"]) == "no"
    assert DOB not in json.dumps(out)
    assert client.post(f"{BASE}/preview", json={"source": {"call_id": "bad"}}).status_code == 400
    other = "CA" + "cd" * 16
    assert client.post(f"{BASE}/preview", json={"source": {"call_id": other}}).status_code == 404


def test_preview_with_an_unsaved_profile_in_the_body(setup):
    client, _ = setup
    profile = example(client)
    items = profile["use_cases"][0]["questionnaire_response"]["items"]
    items["pain_trend"]["codes"]["worse"]["code"] = "much-worse"
    out = client.post(
        f"{BASE}/preview", json={"profile": profile, "source": {"sample": "post_op"}}
    ).json()
    assert out["profile_source"] == "body"
    (row,) = [
        r
        for r in out["source_map"]
        if r.get("question_id") == "pain_trend" and r["target"].endswith("valueCoding")
    ]
    assert resolve(out["bundle"], row["target"])["code"] == "much-worse"
    # Nothing was saved.
    assert client.get(BASE).json()["draft"] is None
    # A body profile that does not parse: 422 with the problems.
    profile["target"]["base_url"] = "ftp://x"
    r = client.post(f"{BASE}/preview", json={"profile": profile, "source": {"sample": "post_op"}})
    assert r.status_code == 422 and r.json()["detail"]["problems"]


def test_simulate_shows_the_second_run_creates_nothing(setup):
    client, api = setup
    store_call(api)
    r = client.post(f"{BASE}/simulate", json={"source": {"call_id": CALL_ID}})
    assert r.status_code == 200, r.text
    out = r.json()
    first, second = out["runs"]
    assert first["created"] == 4 and second["created"] == 0 and second["updated"] == 4
    assert all(set(q) == {"method", "url", "status"} for q in first["requests"])
    assert len(out["resources"]) == 4
    assert DOB not in json.dumps(out)
    # The real outbox record is untouched; nothing is audited.
    rec = run(api.store.get("ehr_outbox", CALL_ID))
    assert rec["status"] == "pending_export" and "ehr_refs" not in rec
    assert audit_actions(api) == []
    sample = client.post(f"{BASE}/simulate", json={"source": {"sample": "post_op"}}).json()
    assert sample["runs"][1]["created"] == 0


# --- outbox -----------------------------------------------------------------------


def test_outbox_list_and_requeue(setup):
    client, api = setup
    store_call(api)
    failed = "CA" + "ef" * 16
    store_call(
        api,
        failed,
        status="failed",
        last_error="patient_not_found: patient not found",
        export_attempts=5,
        export_lease_until="2099-01-01T00:00:00.000+00:00",
        created_at="2026-10-22T10:00:00.000+00:00",
    )
    gone = "CA" + "01" * 16
    store_call(api, gone, status="withdrawn_done")
    rows = client.get(f"{BASE}/outbox").json()["records"]
    assert {r["call_id"] for r in rows} == {CALL_ID, failed, gone}
    assert set(rows[0]) >= {
        "call_id",
        "status",
        "created_at",
        "exported_at",
        "last_error",
        "attempts",
    }
    only = client.get(f"{BASE}/outbox", params={"status": "failed"}).json()["records"]
    assert [r["call_id"] for r in only] == [failed]
    assert only[0]["attempts"] == 5 and only[0]["last_error"].startswith("patient_not_found")
    assert client.get(f"{BASE}/outbox", params={"status": "bogus"}).status_code == 422

    r = client.post(f"{BASE}/outbox/{failed}/requeue", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_export"
    rec = run(api.store.get("ehr_outbox", failed))
    assert rec["status"] == "pending_export" and rec["last_error"] is None
    assert rec["export_attempts"] == 0 and "export_lease_until" not in rec
    assert "POST /api/ehr-export/outbox/{call_id}/requeue" in audit_actions(api)
    assert client.post(f"{BASE}/outbox/{gone}/requeue").status_code == 409
    assert client.post(f"{BASE}/outbox/{'CA' + '99' * 16}/requeue").status_code == 404
    assert client.post(f"{BASE}/outbox/nope/requeue").status_code == 400
    assert DOB not in json.dumps(rows)


# --- access -------------------------------------------------------------------------


@pytest.fixture
def demo_client(monkeypatch):
    monkeypatch.setenv("VOA_ENV", "demo")
    try:
        app = _app("demo")
    except Exception as exc:  # pragma: no cover - config strictness in demo
        pytest.skip(f"demo config not loadable in tests: {exc}")
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", BASE),
        ("GET", f"{BASE}/questions?campaign_id=post_op"),
        ("POST", f"{BASE}/validate"),
        ("PUT", f"{BASE}/draft"),
        ("POST", f"{BASE}/activate"),
        ("GET", f"{BASE}/versions"),
        ("POST", f"{BASE}/preview"),
        ("POST", f"{BASE}/simulate"),
        ("GET", f"{BASE}/outbox"),
        ("POST", f"{BASE}/outbox/{CALL_ID}/requeue"),
    ],
)
def test_a_nurse_gets_403(demo_client, method, path):
    r = demo_client.request(method, path, headers=_token(NURSE_UI), json={})
    assert r.status_code == 403
    r = demo_client.request(method, path, headers=_token(ADMIN_UI), json={})
    assert r.status_code != 403, r.text
