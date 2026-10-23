"""The export loop against the in-memory store and the fake FHIR server. No network."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from ehr_fixtures import DOB, MRN_SYSTEM, ROOT, example_profile, profile_with, sample
from voa_core.store import MemoryStore
from voa_ehr import exporter
from voa_ehr.client import HttpFhirClient, check_connection, client_for
from voa_ehr.fake_fhir import FakeFhirServer

NOW = datetime(2026, 10, 22, 9, tzinfo=UTC)


async def setup(*, patient=True, profile=None, **fake_kw):
    store, fhir = MemoryStore(), FakeFhirServer(**fake_kw)
    call_id, rec, call = sample()
    call["patient_dob"] = DOB  # on the call doc, as in production; must not be sent
    await store.set("ehr_outbox", call_id, rec)
    await store.set("calls", call_id, call)
    pid = fhir.add_patient("A1234567", MRN_SYSTEM, birth_date=DOB) if patient else None
    return store, fhir, call_id, pid, profile or example_profile()


async def run(store, profile, fhir, now=NOW, **kw):
    return await exporter.run_once(store, profile, fhir, now=now, **kw)


def counts(fhir):
    return {t: len(rows) for t, rows in fhir.data.items() if t != "Patient"}


async def test_export_sends_the_bundle_and_writes_back():
    store, fhir, call_id, pid, profile = await setup()
    (out,) = await run(store, profile, fhir)
    assert out.status == "exported"
    rec = await store.get("ehr_outbox", call_id)
    assert rec["status"] == "exported"
    assert rec["export_attempts"] == 1 and rec["last_error"] is None
    assert rec["export_profile"] == {"id": "post_op_example", "version": 1}
    assert "export_lease_until" not in rec
    assert counts(fhir) == {"QuestionnaireResponse": 1, "Task": 1, "Observation": 2}
    assert sorted(rec["ehr_refs"]) == sorted(
        f"{t}/{r['id']}" for t in counts(fhir) for r in fhir.all(t)
    )
    (qr,) = fhir.all("QuestionnaireResponse")
    assert qr["subject"] == {"reference": f"Patient/{pid}"}
    (task,) = fhir.all("Task")
    assert task["focus"] == {"reference": f"QuestionnaireResponse/{qr['id']}"}


async def test_rerun_is_idempotent():
    store, fhir, call_id, _, profile = await setup()
    await run(store, profile, fhir)
    first = {t: [r["id"] for r in fhir.all(t)] for t in counts(fhir)}
    assert await run(store, profile, fhir) == []  # exported: nothing to do
    # The API re-signs it (set, new created_at): the same resources are updated.
    _, rec, _ = sample()
    rec["created_at"] = "2026-10-22T08:00:00.000+00:00"
    await store.set("ehr_outbox", call_id, rec)
    (out,) = await run(store, profile, fhir)
    assert out.status == "exported"
    assert {t: [r["id"] for r in fhir.all(t)] for t in counts(fhir)} == first
    assert fhir.all("QuestionnaireResponse")[0]["meta"]["versionId"] == "2"


async def test_the_date_of_birth_is_never_sent():
    store, fhir, *_, profile = await setup()
    await run(store, profile, fhir)
    assert DOB not in json.dumps(fhir.log)


async def test_birthdate_is_used_in_the_search_only_when_enabled():
    profile = profile_with(patient__search_with_birthdate=True)
    store, fhir, *_ = await setup(profile=profile)
    (out,) = await run(store, profile, fhir)
    assert out.status == "exported"
    searches = [r["url"] for r in fhir.log if r["url"].startswith("Patient?")]
    assert searches and all(f"birthdate={DOB}" in u for u in searches)
    # ...and only in the search, never in a resource.
    assert DOB not in json.dumps([r["body"] for r in fhir.log])


async def test_transcript_is_not_read_or_sent_unless_enabled():
    store, fhir, call_id, _, profile = await setup()
    await store.set("transcripts", call_id, {"turns": [{"role": "patient", "text": "knee hurts"}]})
    reads = []
    real_get = store.get

    async def get(coll, doc_id):
        reads.append(coll)
        return await real_get(coll, doc_id)

    store.get = get
    await run(store, profile, fhir)
    assert "transcripts" not in reads
    assert "knee hurts" not in json.dumps(fhir.log)
    on = profile_with(uc__transcript={"enabled": True, "phi_acknowledged": True})
    _, rec, _ = sample()
    rec["created_at"] = "2026-10-22T08:00:00.000+00:00"
    await store.set("ehr_outbox", call_id, rec)
    await run(store, on, fhir)
    assert "transcripts" in reads
    assert len(fhir.all("DocumentReference")) == 1


# --- patient lookup -------------------------------------------------------------------


async def test_patient_not_found_retries_then_fails():
    store, fhir, call_id, _, profile = await setup(patient=False)
    for i in range(1, 3):
        (out,) = await run(store, profile, fhir, max_attempts=3)
        rec = await store.get("ehr_outbox", call_id)
        assert rec["status"] == "pending_export" and rec["export_attempts"] == i
        assert rec["last_error"].startswith("patient_not_found")
    (out,) = await run(store, profile, fhir, max_attempts=3)
    assert out.status == "failed"
    assert counts(fhir) == {}


async def test_patient_not_found_fail_and_skip():
    profile = profile_with(patient__not_found="fail")
    store, fhir, call_id, *_ = await setup(patient=False, profile=profile)
    (out,) = await run(store, profile, fhir)
    assert out.status == "failed"
    profile = profile_with(patient__not_found="skip")
    store, fhir, call_id, *_ = await setup(patient=False, profile=profile)
    (out,) = await run(store, profile, fhir)
    assert out.status == "skipped"
    assert (await store.get("ehr_outbox", call_id))["status"] == "skipped"


async def test_two_matching_patients_is_never_guessed():
    store, fhir, call_id, _, profile = await setup()
    fhir.add_patient("A1234567", MRN_SYSTEM)
    (out,) = await run(store, profile, fhir)
    assert out.status == "failed" and "ambiguous" in out.error
    assert counts(fhir) == {}


async def test_no_mrn_fails_without_a_search():
    store, fhir, call_id, _, profile = await setup()
    rec = await store.get("ehr_outbox", call_id)
    rec["patient"]["mrn"] = None
    await store.set("ehr_outbox", call_id, rec)
    (out,) = await run(store, profile, fhir)
    assert out.status == "failed" and "no_mrn" in out.error
    assert fhir.log == []


async def test_no_mapping_for_the_use_case_is_skipped():
    store, fhir, call_id, _, profile = await setup()
    await store.merge("calls", call_id, {"campaign_id": "pre_op"})
    (out,) = await run(store, profile, fhir)
    assert out.status == "skipped" and "no_mapping" in out.error


# --- failures and races ---------------------------------------------------------------


async def test_a_transient_failure_is_retried():
    store, fhir, call_id, _, profile = await setup()
    fhir.fail_next = [503]  # the patient search fails once
    (out,) = await run(store, profile, fhir)
    assert out.status == "pending_export"
    (out,) = await run(store, profile, fhir)
    assert out.status == "exported"
    assert (await store.get("ehr_outbox", call_id))["export_attempts"] == 2


async def test_a_leased_record_is_not_sent_twice():
    store, fhir, call_id, _, profile = await setup()
    await store.merge(
        "ehr_outbox", call_id, {"export_lease_until": (NOW + timedelta(minutes=2)).isoformat()}
    )
    assert await run(store, profile, fhir) == []
    assert len(await run(store, profile, fhir, now=NOW + timedelta(minutes=3))) == 1


async def test_a_withdrawal_during_the_send_is_not_overwritten():
    store, fhir, call_id, _, profile = await setup()
    real = fhir.request

    async def racing(method, url, **kw):
        r = await real(method, url, **kw)
        if method == "POST":  # the transaction: the API reopens the review meanwhile
            await store.merge("ehr_outbox", call_id, {"status": "withdrawn"})
        return r

    fhir.request = racing
    await run(store, profile, fhir)
    assert (await store.get("ehr_outbox", call_id))["status"] == "withdrawn"
    fhir.request = real
    (out,) = await run(store, profile, fhir)
    assert out.status == "withdrawn_done"
    assert {r["status"] for t in counts(fhir) for r in fhir.all(t)} == {"entered-in-error"}


# --- withdrawals ----------------------------------------------------------------------


async def test_withdrawn_marks_everything_entered_in_error():
    store, fhir, call_id, _, profile = await setup()
    await run(store, profile, fhir)
    await store.merge("ehr_outbox", call_id, {"status": "withdrawn", "withdrawn_at": "x"})
    (out,) = await run(store, profile, fhir)
    assert out.status == "withdrawn_done" and len(out.refs) == 4
    for t in counts(fhir):
        assert {r["status"] for r in fhir.all(t)} == {"entered-in-error"}
    rec = await store.get("ehr_outbox", call_id)
    assert rec["status"] == "withdrawn_done" and sorted(rec["entered_in_error"]) == sorted(out.refs)
    assert counts(fhir) == {"QuestionnaireResponse": 1, "Task": 1, "Observation": 2}
    assert await run(store, profile, fhir) == []


async def test_withdrawn_before_export_sends_nothing():
    store, fhir, call_id, _, profile = await setup()
    await store.merge("ehr_outbox", call_id, {"status": "withdrawn"})
    (out,) = await run(store, profile, fhir)
    assert out.status == "withdrawn_done" and out.refs == []
    assert counts(fhir) == {}
    assert not [r for r in fhir.log if r["method"] in ("PUT", "POST")]


async def test_re_sign_off_after_withdrawal_restores_the_resources():
    store, fhir, call_id, _, profile = await setup()
    await run(store, profile, fhir)
    await store.merge("ehr_outbox", call_id, {"status": "withdrawn"})
    await run(store, profile, fhir)
    _, rec, _ = sample()
    rec["created_at"] = "2026-10-22T10:00:00.000+00:00"
    await store.set("ehr_outbox", call_id, rec)
    await run(store, profile, fhir)
    assert fhir.all("QuestionnaireResponse")[0]["status"] == "completed"
    assert counts(fhir) == {"QuestionnaireResponse": 1, "Task": 1, "Observation": 2}


# --- submit modes ---------------------------------------------------------------------


@pytest.mark.parametrize("write_mode", ["conditional_update", "conditional_create"])
async def test_individual_requests_for_servers_without_transactions(write_mode):
    profile = profile_with(target__submit="individual", target__write_mode=write_mode)
    store, fhir, call_id, *_ = await setup(profile=profile, transactions=False)
    (out,) = await run(store, profile, fhir)
    assert out.status == "exported", out.error
    (qr,) = fhir.all("QuestionnaireResponse")
    assert fhir.all("Task")[0]["focus"] == {"reference": f"QuestionnaireResponse/{qr['id']}"}
    assert all(
        o["derivedFrom"] == [{"reference": f"QuestionnaireResponse/{qr['id']}"}]
        for o in fhir.all("Observation")
    )
    _, rec, _ = sample()
    rec["created_at"] = "2026-10-22T10:00:00.000+00:00"
    await store.set("ehr_outbox", call_id, rec)
    await run(store, profile, fhir)
    assert counts(fhir) == {"QuestionnaireResponse": 1, "Task": 1, "Observation": 2}


async def test_a_transaction_to_a_server_without_them_fails_clearly():
    store, fhir, call_id, _, profile = await setup(transactions=False)
    (out,) = await run(store, profile, fhir)
    assert out.status == "pending_export" and "HTTP 501" in out.error


# --- preview, HTTP client, connection test --------------------------------------------


async def test_preview_matches_what_is_sent():
    store, fhir, call_id, pid, profile = await setup()
    rec = await store.get("ehr_outbox", call_id)
    pv = await exporter.preview(store, profile, rec, call_id=call_id, patient_id=pid)
    assert pv["ok"] and pv["errors"] == []
    await run(store, profile, fhir)
    (tx,) = [r for r in fhir.log if r["method"] == "POST"]
    assert tx["body"] == pv["bundle"]


async def test_http_client_against_the_fake_transport():
    store, fhir, call_id, _, profile = await setup()
    profile = profile_with(target__base_url="http://localhost:8080/fhir")
    client = client_for(profile, lambda ref: "test-token", transport=fhir.transport())
    try:
        assert (await check_connection(client))["ok"]
        (out,) = await run(store, profile, client)
        assert out.status == "exported"
    finally:
        await client.aclose()
    assert all(r["headers"].get("authorization") == "Bearer test-token" for r in fhir.log)
    assert counts(fhir) == {"QuestionnaireResponse": 1, "Task": 1, "Observation": 2}


async def test_check_connection_reports_failures():
    def boom(req):
        raise httpx.ConnectError("no route")

    client = HttpFhirClient("http://localhost:1/fhir", transport=httpx.MockTransport(boom))
    assert await check_connection(client) == {"ok": False, "error": "ConnectError"}
    await client.aclose()


def test_unbuilt_auth_types_are_refused_clearly():
    data = example_profile().model_dump(mode="json")
    data["target"]["auth"] = {
        "type": "client_credentials",
        "secret": "secret://ehr-client-secret",
        "client_id": "voa",
        "token_url": "https://fhir.example.org/oauth2/token",
    }
    from voa_ehr.profile import parse_profile

    with pytest.raises(NotImplementedError):
        client_for(parse_profile(data), lambda ref: "x")


def test_preview_script(capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ehr_export_preview", ROOT / "scripts/ehr_export_preview.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (
        mod.main(
            [
                str(ROOT / "config/ehr/profiles/post_op.example.yaml"),
                str(ROOT / "config/ehr/samples/post_op.outbox.json"),
            ]
        )
        == 0
    )
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["type"] == "transaction" and len(bundle["entry"]) == 4
