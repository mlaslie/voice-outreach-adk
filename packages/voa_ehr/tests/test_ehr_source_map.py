"""The source map: every mapped value traced back to the outbox record (plan §4.2).

Both sides of each entry are JSON Pointers and must resolve; every item the
bundle carries must have a source; every answer is either mapped or listed as
skipped with its own pointer.
"""

import json

import pytest
from ehr_fixtures import example_data, example_profile, profile_with, record_with_answers, sample
from voa_ehr import exporter, transform
from voa_ehr.fhir_check import fhir_problem_details, fhir_problems
from voa_ehr.profile import parse_profile

_MISSING = object()


def resolve(doc, ptr):
    """RFC 6901; _MISSING when the pointer does not resolve."""
    if ptr == "":
        return doc
    assert ptr.startswith("/"), ptr
    cur = doc
    for raw in ptr[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


async def pv(profile=None, rec=None, call=None, **kw):
    call_id, srec, scall = sample()
    return await exporter.preview(
        None,
        profile or example_profile(),
        rec if rec is not None else srec,
        call_id=call_id,
        call=call if call is not None else scall,
        **kw,
    )


def check_pointers(result, record):
    for row in result["source_map"]:
        assert set(row) <= {"source", "target", "question_id", "note"}
        assert resolve(result["bundle"], row["target"]) is not _MISSING, row
        if row["source"] is not None:
            assert resolve(record, row["source"]) is not _MISSING, row
    for s in result["skipped"]:
        assert set(s) == {"question_id", "reason", "source"}
        if s["source"] is not None:
            assert resolve(record, s["source"]) is not _MISSING, s


def check_coverage(result, record):
    """Every QR item and answer in the bundle has a source; every answered record
    item is mapped somewhere or listed as skipped."""
    targets = {r["target"] for r in result["source_map"]}
    for i, entry in enumerate(result["bundle"]["entry"]):
        res = entry["resource"]
        if res["resourceType"] == "QuestionnaireResponse":
            for j, item in enumerate(res.get("item", [])):
                assert f"/entry/{i}/resource/item/{j}" in targets
                for k, ans in enumerate(item["answer"]):
                    (key,) = ans
                    assert f"/entry/{i}/resource/item/{j}/answer/{k}/{key}" in targets
            assert f"/entry/{i}/resource/subject" in targets
            assert f"/entry/{i}/resource/identifier/value" in targets
        if res["resourceType"] == "Observation":
            assert any(t.startswith(f"/entry/{i}/resource/value") for t in targets), (
                f"observation {i} has no source"
            )
    sources = [r["source"] or "" for r in result["source_map"]]
    skipped = {s["source"] for s in result["skipped"]}
    for n, item in enumerate(record["questionnaire_response"]["item"]):
        ptr = f"/questionnaire_response/item/{n}/answer"
        if not transform.answered(item.get("answer")):
            continue
        mapped = any(s == ptr or s.startswith(ptr + "/") for s in sources)
        assert mapped or ptr in skipped, item["linkId"]


async def test_the_sample_is_fully_traced():
    _, rec, _ = sample()
    r = await pv()
    assert r["ok"] and r["errors"] == []
    check_pointers(r, r["source_record"])
    check_coverage(r, rec)
    by_target = {row["target"]: row for row in r["source_map"]}
    # pain_trend "worse" -> item 2.3 valueCoding, the plan's example.
    (i,) = [
        n
        for n, it in enumerate(rec["questionnaire_response"]["item"])
        if it["linkId"] == "pain_trend"
    ]
    (j,) = [
        n
        for n, it in enumerate(r["bundle"]["entry"][0]["resource"]["item"])
        if it["linkId"] == "2.3"
    ]
    row = by_target[f"/entry/0/resource/item/{j}/answer/0/valueCoding"]
    assert row["source"] == f"/questionnaire_response/item/{i}/answer"
    assert row["question_id"] == "pain_trend"
    task = next(
        n for n, e in enumerate(r["bundle"]["entry"]) if e["resource"]["resourceType"] == "Task"
    )
    t = f"/entry/{task}/resource"
    assert by_target[f"{t}/priority"]["source"] == "/disposition/follow_up"
    assert by_target[f"{t}/status"]["source"] == "/review/resolved"
    assert by_target[f"{t}/note/0/text"]["source"] == "/review/note"
    assert by_target[f"{t}/owner"]["source"] == "/review/by"
    assert by_target[f"{t}/for"]["note"] == "patient lookup (placeholder in preview)"
    assert by_target["/entry/0/resource/identifier/value"]["source"] == "/call_sid"
    obs = [r2 for r2 in r["source_map"] if r2.get("question_id") == "pain_severity"]
    assert any("/valueInteger" in o["target"] for o in obs)


async def test_the_source_map_does_not_change_the_bundle():
    call_id, rec, call = sample()
    profile = example_profile()
    uc = profile.for_use_case("post_op", 1)
    patient = {"reference": "Patient/example-patient"}
    export = transform.build_export(rec, profile, uc, patient, call_id=call_id, call=call)
    r = await pv(profile)
    assert json.dumps(r["bundle"]) == json.dumps(export.bundle)
    assert r["source_map"] == export.source_map


async def test_skipped_answers_carry_their_source():
    data = example_data()
    del data["use_cases"][0]["questionnaire_response"]["items"]["sleeping_well"]
    data["use_cases"][0]["questionnaire_response"]["exclude"] = ["overall_feeling"]
    _, rec, _ = sample()
    r = await pv(parse_profile(data))
    check_pointers(r, rec)
    check_coverage(r, rec)
    by_q = {s["question_id"]: s for s in r["skipped"]}
    idx = {it["linkId"]: n for n, it in enumerate(rec["questionnaire_response"]["item"])}
    assert by_q["sleeping_well"] == {
        "question_id": "sleeping_well",
        "reason": "not mapped",
        "source": f"/questionnaire_response/item/{idx['sleeping_well']}/answer",
    }
    assert by_q["overall_feeling"]["reason"] == "excluded by the profile"


@pytest.mark.parametrize(
    "variant",
    [
        "unmapped_text",
        "untypeable_text",
        "concat",
        "constant",
        "documents",
        "communication",
        "value_map",
    ],
)
async def test_every_pointer_resolves(variant):
    data = example_data()
    uc = data["use_cases"][0]
    items = uc["questionnaire_response"]["items"]
    rec = None
    transcript = None
    if variant == "unmapped_text":
        del items["sleeping_well"], items["patient_questions"]
        uc["questionnaire_response"].update(unmapped="text", unmapped_prefix="voa.")
    elif variant == "untypeable_text":
        items["pain_severity"] = {"link_id": "2.1", "type": "integer"}
        uc["questionnaire_response"].update(unmapped="text", unmapped_prefix="voa.")
        _, rec, _ = sample()
        rec = record_with_answers({"pain_severity": "about seven", "pain_trend": "worse"})
    elif variant == "concat":
        items["pain_location"] = {
            "link_id": "2.2",
            "type": "string",
            "concat": ["pain_location", "overall_feeling"],
        }
    elif variant == "constant":
        items["pain_present"] = {"link_id": "2", "type": "string", "constant": "reported"}
    elif variant in ("documents", "communication"):
        res = "Communication" if variant == "communication" else "DocumentReference"
        for name in ("summary", "transcript"):
            uc[name] = {"enabled": True, "phi_acknowledged": True, "resource": res}
        transcript = [{"role": "agent", "text": "Hello"}, {"role": "patient", "text": "Hi"}]
    elif variant == "value_map":
        items["pain_trend"]["value_map"] = {"worse": "same"}
    profile = parse_profile(data)
    call_id, srec, call = sample()
    record = rec if rec is not None else srec
    uc_map = profile.for_use_case("post_op", 1)
    export = transform.build_export(
        record,
        profile,
        uc_map,
        {"reference": "Patient/p1"},
        call_id=call_id,
        call=call,
        transcript=transcript,
    )
    result = {
        "bundle": export.bundle,
        "source_map": export.source_map,
        "skipped": [
            {
                "question_id": s["question"],
                "reason": s["reason"],
                "source": transform.answer_sources(record).get(s["question"]),
            }
            for s in export.skipped
        ],
    }
    check_pointers(result, record)
    check_coverage(result, record)
    if variant in ("documents", "communication"):
        notes = [r.get("note", "") for r in export.source_map]
        assert any(n.startswith("transcript") for n in notes)
        assert any(n == "summary text" for n in notes)
        # The transcript comes from transcripts/{id}, outside the record.
        assert any(r["source"] is None for r in export.source_map)
    if variant == "concat":
        srcs = [r for r in export.source_map if r.get("question_id") == "overall_feeling"]
        assert srcs, "concat parts are traced"


async def test_answers_from_the_call_doc_have_no_source_pointer():
    call_id, rec, call = sample()
    rec["questionnaire_response"]["item"] = []
    r = await pv(rec=rec, call=call)
    assert r["bundle"]["entry"][0]["resource"]["item"]
    answer_rows = [row for row in r["source_map"] if "question_id" in row]
    assert answer_rows and all(row["source"] is None for row in answer_rows)


async def test_source_record_never_carries_a_dob():
    call_id, rec, call = sample()
    rec["patient"]["dob"] = "1950-03-15"
    rec["patient_dob"] = "1950-03-15"
    r = await pv(rec=rec)
    assert "1950-03-15" not in json.dumps(r)
    assert "dob" not in r["source_record"]["patient"]


async def test_no_mapping_has_the_contract_shape():
    r = await pv(profile_with(uc__campaign_id="post_op", uc__spec_versions=[99]))
    assert r["ok"] is False and r["bundle"] is None
    assert set(r) == {
        "ok",
        "source_record",
        "bundle",
        "source_map",
        "skipped",
        "warnings",
        "errors",
    }
    assert r["errors"][0]["message"]


def test_fhir_errors_carry_a_path_into_the_bundle():
    call_id, rec, call = sample()
    profile = example_profile()
    uc = profile.for_use_case("post_op", 1)
    bundle = transform.build_export(
        rec, profile, uc, {"reference": "Patient/p1"}, call_id=call_id, call=call
    ).bundle
    bundle["entry"][1]["resource"]["status"] = "done"  # not a Task status
    bundle["entry"][0]["resource"]["item"][0]["bogus"] = 1  # not an element
    del bundle["entry"][1]["resource"]["intent"]  # required
    details = fhir_problem_details(bundle)
    assert [d["message"] for d in details] == fhir_problems(bundle)
    paths = {d["path"] for d in details}
    assert "/entry/1/resource/status" in paths
    assert "/entry/0/resource/item/0/bogus" in paths
    assert "/entry/1/resource" in paths  # missing intent: its parent
    for d in details:
        assert resolve(bundle, d["path"]) is not _MISSING


async def test_simulate_twice_creates_nothing_the_second_time():
    call_id, rec, call = sample()
    call["patient_dob"] = "1950-03-15"
    out = await exporter.simulate(example_profile(), rec, call_id=call_id, call=call)
    first, second = out["runs"]
    assert first["outcome"]["status"] == "exported"
    assert first["created"] == 4 and first["updated"] == 0
    assert second["outcome"]["status"] == "exported"
    assert second["created"] == 0 and second["updated"] == 4
    assert {r["method"] for r in first["requests"]} == {"GET", "POST"}
    assert all(set(r) == {"method", "url", "status"} for r in first["requests"])
    assert len(out["resources"]) == 4
    assert "1950-03-15" not in json.dumps(out)
    # the caller's record is not touched
    assert rec["status"] == "pending_export" and "ehr_refs" not in rec
