"""The pure mapping: outbox record + profile -> FHIR resources."""

import base64
import json

import pytest
from ehr_fixtures import (
    CALL_SYSTEM,
    DOB,
    example_data,
    example_profile,
    profile_with,
    record_with_answers,
    sample,
)
from voa_ehr import transform
from voa_ehr.fhir_check import fhir_problems
from voa_ehr.profile import ItemMap, parse_profile

PATIENT = {"reference": "Patient/p1"}


def build(profile=None, rec=None, call=None, transcript=None):
    profile = profile or example_profile()
    call_id, srec, scall = sample()
    rec = rec if rec is not None else srec
    uc = profile.for_use_case("post_op", 1)
    return transform.build_export(
        rec, profile, uc, PATIENT, call_id=call_id, call=call or scall, transcript=transcript
    )


def of_type(export, rtype):
    return [r for r in export.resources if r["resourceType"] == rtype]


def items(export):
    return {i["linkId"]: i["answer"] for i in of_type(export, "QuestionnaireResponse")[0]["item"]}


# --- per answer type ------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,answer,expected",
    [
        ({"type": "boolean"}, "yes", [{"valueBoolean": True}]),
        ({"type": "boolean"}, "No", [{"valueBoolean": False}]),
        ({"type": "boolean"}, True, [{"valueBoolean": True}]),
        ({"type": "integer"}, 7, [{"valueInteger": 7}]),
        ({"type": "integer"}, "7", [{"valueInteger": 7}]),
        ({"type": "decimal"}, "101.5", [{"valueDecimal": 101.5}]),
        ({"type": "string"}, "  sore   knee ", [{"valueString": "sore knee"}]),
        ({"type": "date"}, "2026-10-01", [{"valueDate": "2026-10-01"}]),
        (
            {"type": "date", "date_formats": ["%Y-%m-%d", "%m/%d/%Y"]},
            "10/01/2026",
            [{"valueDate": "2026-10-01"}],
        ),
        (
            {"type": "quantity", "unit": "[degF]"},
            101,
            [
                {
                    "valueQuantity": {
                        "value": 101,
                        "unit": "[degF]",
                        "system": "http://unitsofmeasure.org",
                        "code": "[degF]",
                    }
                }
            ],
        ),
        (
            {"type": "coding", "system": "urn:x", "codes": {"worse": {"code": "W"}}},
            "Worse",
            [{"valueCoding": {"system": "urn:x", "code": "W"}}],
        ),
    ],
)
def test_each_answer_type(spec, answer, expected):
    typed, bad = transform.values_for(ItemMap(link_id="q", **spec), "q", {"q": answer})
    assert typed == expected and bad == []


@pytest.mark.parametrize(
    "spec,answer",
    [
        ({"type": "boolean"}, "unknown"),
        ({"type": "integer"}, 7.5),
        ({"type": "integer"}, True),
        ({"type": "integer"}, "about seven"),
        ({"type": "date"}, "last Tuesday"),
        ({"type": "coding", "system": "urn:x", "codes": {"worse": {"code": "W"}}}, "sideways"),
    ],
)
def test_values_that_cannot_be_typed_are_reported_not_guessed(spec, answer):
    typed, bad = transform.values_for(ItemMap(link_id="q", **spec), "q", {"q": answer})
    assert typed == [] and bad == [answer]


def test_unanswered_questions_send_nothing():
    for v in (None, "", "   ", []):
        assert transform.values_for(ItemMap(link_id="q", type="string"), "q", {"q": v}) == ([], [])


def test_transforms_value_map_constant_concat():
    vm = ItemMap(link_id="q", type="integer", value_map={"better": -1, "same": 0, "worse": 1})
    assert transform.values_for(vm, "q", {"q": "worse"})[0] == [{"valueInteger": 1}]
    const = ItemMap(link_id="q", type="string", constant="reported by phone")
    assert transform.values_for(const, "q", {"q": "anything"})[0] == [
        {"valueString": "reported by phone"}
    ]
    cat = ItemMap(link_id="q", type="string", concat=["a", "b", "c"], separator=" / ")
    assert transform.values_for(cat, "q", {"a": "left calf", "c": "worse"})[0] == [
        {"valueString": "left calf / worse"}
    ]


def test_the_sample_maps_every_answer_type():
    got = items(build())
    assert got["2"] == [{"valueBoolean": True}]  # pain_present yes
    assert got["2.1"] == [{"valueInteger": 8}]
    assert got["2.3"][0]["valueCoding"]["code"] == "worsening"
    assert got["3"][0]["valueCoding"]["code"] == "LA32-8"  # fever no
    assert got["5"][0]["valueCoding"]["code"] == "LA4489-6"  # swelling unknown
    assert got["8.1"] == [{"valueString": "When can I shower?"}]  # patient_questions


def test_code_maps_and_value_maps_combine():
    data = example_data()
    items_ = data["use_cases"][0]["questionnaire_response"]["items"]
    items_["pain_trend"]["value_map"] = {"same": "worse"}  # a deliberately odd map
    rec = record_with_answers({"pain_trend": "same"})
    got = items(build(parse_profile(data), rec))
    assert got["2.3"][0]["valueCoding"]["code"] == "worsening"


# --- unmapped -----------------------------------------------------------------------


def test_an_unmapped_question_is_skipped_by_default():
    data = example_data()
    del data["use_cases"][0]["questionnaire_response"]["items"]["sleeping_well"]
    export = build(parse_profile(data))
    assert "6" not in items(export)
    assert {"question": "sleeping_well", "reason": "not mapped"} in export.skipped
    assert "sleeping_well" not in json.dumps(export.bundle)


def test_an_unmapped_question_can_be_sent_as_text():
    data = example_data()
    qr = data["use_cases"][0]["questionnaire_response"]
    del qr["items"]["sleeping_well"]
    qr["unmapped"], qr["unmapped_prefix"] = "text", "app."
    got = items(build(parse_profile(data)))
    assert got["app.sleeping_well"] == [{"valueString": "no"}]


def test_an_untypeable_mapped_answer_follows_the_unmapped_setting():
    data = example_data()
    qr = data["use_cases"][0]["questionnaire_response"]
    qr["items"]["legs_swollen"] = {"link_id": "5", "type": "boolean"}
    export = build(parse_profile(data))
    assert "5" not in items(export)  # "unknown" is not a boolean
    assert any(s["question"] == "legs_swollen" for s in export.skipped)
    qr["unmapped"] = "text"
    assert items(build(parse_profile(data)))["legs_swollen"] == [{"valueString": "unknown"}]


def test_excluded_questions_are_never_sent():
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["exclude"] = ["overall_feeling"]
    data["use_cases"][0]["questionnaire_response"]["unmapped"] = "text"
    export = build(parse_profile(data))
    assert "Tired" not in json.dumps(export.bundle)


# --- Task -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,priority",
    [
        ("emergency", "stat"),
        ("urgent_same_day", "urgent"),
        ("routine_next_business_day", "routine"),
        ("informational", "routine"),
    ],
)
def test_task_priority_follows_the_tier(tier, priority):
    _, rec, _ = sample()
    rec["disposition"]["follow_up"] = tier
    (task,) = of_type(build(rec=rec), "Task")
    assert task["priority"] == priority
    assert task["status"] == "completed"
    assert task["for"] == PATIENT


def test_task_priority_map_is_configurable():
    p = profile_with(task__priority={"urgent_same_day": "asap"})
    assert of_type(build(p), "Task")[0]["priority"] == "asap"


def test_no_task_when_nothing_was_resolved():
    _, rec, _ = sample()
    rec["review"]["resolved"] = False
    rec["disposition"]["follow_up"] = "none"
    assert of_type(build(rec=rec), "Task") == []
    p = profile_with(task__emit_when="always")
    (task,) = of_type(build(p, rec), "Task")
    assert task["status"] == "requested"


def test_task_points_at_the_questionnaire_response_and_carries_the_note():
    export = build()
    qr_url = export.entries[0].full_url
    (task,) = of_type(export, "Task")
    assert task["focus"] == {"reference": qr_url}
    assert task["note"][0]["text"].startswith("Called back")
    assert task["owner"]["identifier"]["value"] == "nurse.one@example.org"


def test_task_owner_modes():
    p = profile_with(task__owner={"mode": "display"})
    assert of_type(build(p), "Task")[0]["owner"] == {"display": "nurse.one@example.org"}
    p = profile_with(task__owner={"mode": "none"})
    assert "owner" not in of_type(build(p), "Task")[0]


# --- Observations ---------------------------------------------------------------------


def test_observations_for_selected_answers():
    obs = {o["code"]["coding"][0]["code"]: o for o in of_type(build(), "Observation")}
    assert obs["72514-3"]["valueInteger"] == 8
    assert obs["386661006"]["valueBoolean"] is False
    assert obs["72514-3"]["derivedFrom"][0]["reference"].startswith("urn:uuid:")


def test_no_observation_when_the_answer_is_missing():
    rec = record_with_answers({"pain_present": "no", "fever_reported": "unknown"})
    assert of_type(build(rec=rec), "Observation") == []


def test_coded_observation_values_become_codeable_concepts():
    data = example_data()
    data["use_cases"][0]["observations"][1].update(
        type="coding",
        system="http://snomed.info/sct",
        codes={"yes": {"code": "386661006"}, "no": {"code": "260385009"}},
    )
    fever = of_type(build(parse_profile(data)), "Observation")[1]
    assert fever["valueCodeableConcept"]["coding"][0]["code"] == "260385009"


# --- PHI: DOB and transcript ----------------------------------------------------------


def test_never_sends_the_date_of_birth():
    _, rec, call = sample()
    call["patient_dob"] = DOB
    rec["questionnaire_response"]["item"].append({"linkId": "patient_dob", "answer": DOB})
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["unmapped"] = "text"
    data["use_cases"][0]["summary"] = {"enabled": True, "phi_acknowledged": True}
    export = build(parse_profile(data), rec, call)
    assert DOB not in json.dumps(export.bundle)
    assert "1950" not in json.dumps(export.bundle)


def test_transcript_is_not_sent_unless_enabled():
    turns = [{"role": "patient", "text": "my knee hurts a lot"}]
    assert "knee hurts" not in json.dumps(build(transcript=turns).bundle)
    p = profile_with(
        uc__transcript={"enabled": True, "phi_acknowledged": True, "language": "english"}
    )
    export = build(p, transcript=[{"role": "patient", "text": "me duele", "text_en": "it hurts"}])
    (doc,) = [e.resource for e in export.entries if e.ident.endswith("-transcript")]
    text = base64.b64decode(doc["content"][0]["attachment"]["data"]).decode()
    assert text == "Patient: it hurts"


def test_summary_is_off_by_default_and_can_be_a_communication():
    assert not [e for e in build().entries if e.ident.endswith("-summary")]
    p = profile_with(
        uc__summary={"enabled": True, "phi_acknowledged": True, "resource": "Communication"}
    )
    (comm,) = of_type(build(p), "Communication")
    assert "Follow-up tier: urgent_same_day" in comm["payload"][0]["contentString"]


# --- identifiers, requests, validity --------------------------------------------------


def test_every_resource_has_the_call_identifier_and_a_conditional_request():
    export = build()
    call_id, _, _ = sample()
    for e, entry in zip(export.entries, export.bundle["entry"], strict=True):
        idents = e.resource["identifier"]
        idents = idents if isinstance(idents, list) else [idents]
        assert idents[0] == {"system": CALL_SYSTEM, "value": e.ident}
        assert e.ident.startswith(call_id)
        assert entry["request"]["method"] == "PUT"
        assert entry["request"]["url"].startswith(f"{e.resource_type}?identifier=")


def test_conditional_create_mode():
    p = profile_with(target__write_mode="conditional_create")
    req = build(p).bundle["entry"][0]["request"]
    assert req["method"] == "POST" and req["ifNoneExist"].startswith("identifier=")


def test_bundles_are_deterministic():
    assert build().bundle == build().bundle


def test_the_output_is_valid_fhir_r4_structure():
    p = profile_with(
        uc__summary={"enabled": True, "phi_acknowledged": True},
        uc__transcript={"enabled": True, "phi_acknowledged": True},
    )
    export = build(p, transcript=[{"role": "agent", "text": "Hello"}])
    assert fhir_problems(export.bundle) == []
    for r in export.resources:
        assert fhir_problems(transform.entered_in_error(r)) == []


def test_fhir_check_catches_shape_and_code_errors():
    bad = {"resourceType": "QuestionnaireResponse", "status": "done", "identifier": [{}]}
    problems = fhir_problems(bad)
    assert any("identifier" in p for p in problems)
    assert any("status" in p for p in problems)
    assert fhir_problems({"resourceType": "Task", "status": "completed"})  # intent required
