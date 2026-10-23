"""The export profile: the model, loading, and the checks against the use-case specs."""

import pytest
from ehr_fixtures import EXAMPLE_PROFILE, example_data, example_profile, profile_with
from voa_ehr.profile import (
    ProfileError,
    load_profile,
    parse_profile,
    profile_from_text,
    profile_problems,
    profile_to_json,
    profile_to_yaml,
)


def errors(profile):
    return [p for p in profile_problems(profile) if p.is_error]


def test_the_worked_example_loads_with_no_errors():
    p = load_profile(EXAMPLE_PROFILE)
    assert p.id == "post_op_example"
    assert p.target.auth.secret == "secret://ehr-fhir-token"
    assert not errors(p)
    uc = p.for_use_case("post_op", 1)
    assert uc is not None and "pain_severity" in uc.questionnaire_response.items


def test_yaml_and_json_round_trip():
    p = example_profile()
    assert profile_from_text(profile_to_yaml(p)) == p
    assert profile_from_text(profile_to_json(p)) == p


def test_an_unknown_question_id_is_an_error():
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["items"]["pain_colour"] = {
        "link_id": "99",
        "type": "string",
    }
    errs = errors(parse_profile(data))
    assert any("'pain_colour' is not a question in post_op" in e.message for e in errs)


def test_load_profile_raises_on_spec_errors(tmp_path):
    text = EXAMPLE_PROFILE.read_text().replace("source: pain_severity", "source: pain_sevrity")
    f = tmp_path / "bad.yaml"
    f.write_text(text)
    with pytest.raises(ProfileError) as e:
        load_profile(f)
    assert "pain_sevrity" in str(e.value)


@pytest.mark.parametrize(
    "qid,spec",
    [
        ("pain_present", {"link_id": "x", "type": "integer"}),  # yes/no as a number
        ("pain_trend", {"link_id": "x", "type": "boolean"}),  # a choice as a boolean
        ("overall_feeling", {"link_id": "x", "type": "date"}),  # free text as a date
    ],
)
def test_a_type_that_does_not_fit_the_question_is_an_error(qid, spec):
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["items"][qid] = spec
    errs = errors(parse_profile(data))
    assert any(f"does not fit {qid!r}" in e.message for e in errs)


def test_an_unknown_answer_type_is_refused_by_the_model():
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["items"]["pain_severity"]["type"] = "float"
    with pytest.raises(ProfileError) as e:
        parse_profile(data)
    assert any("pain_severity" in p.path for p in e.value.problems)


def test_misspelt_keys_are_errors_not_ignored():
    data = example_data()
    data["patient"]["identifer_system"] = "x"
    with pytest.raises(ProfileError):
        parse_profile(data)


def test_credentials_are_references_only():
    data = example_data()
    data["target"]["auth"]["secret"] = "eyJhbGciOi-a-real-looking-token"
    with pytest.raises(ProfileError) as e:
        parse_profile(data)
    assert "secret://" in str(e.value)
    data["target"]["auth"] = {"type": "bearer", "token": "abc"}
    with pytest.raises(ProfileError):
        parse_profile(data)


@pytest.mark.parametrize(
    "url",
    ["http://fhir.example.org/R4", "https://user:pw@fhir.example.org", "https://x.org/?a=1"],
)
def test_base_url_must_be_plain_https(url):
    with pytest.raises(ProfileError):
        profile_with(target__base_url=url)


def test_http_is_allowed_for_localhost_only():
    assert profile_with(target__base_url="http://localhost:8080/fhir").target.base_url


def test_coding_needs_codes_and_a_system():
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["items"]["pain_trend"] = {
        "link_id": "2.3",
        "type": "coding",
        "codes": {"worse": {"code": "w"}},
    }
    with pytest.raises(ProfileError) as e:
        parse_profile(data)
    assert "system" in str(e.value)


def test_units_belong_to_quantity():
    data = example_data()
    items = data["use_cases"][0]["questionnaire_response"]["items"]
    items["pain_severity"] = {"link_id": "2.1", "type": "integer", "unit": "score"}
    with pytest.raises(ProfileError):
        parse_profile(data)
    items["pain_severity"] = {"link_id": "2.1", "type": "quantity"}
    with pytest.raises(ProfileError):
        parse_profile(data)
    items["pain_severity"] = {"link_id": "2.1", "type": "quantity", "unit": "{score}"}
    assert not errors(parse_profile(data))


def test_date_formats_are_restricted_to_date_directives():
    data = example_data()
    items = data["use_cases"][0]["questionnaire_response"]["items"]
    items["overall_feeling"] = {"link_id": "1", "type": "string", "date_formats": ["%H:%M"]}
    with pytest.raises(ProfileError):
        parse_profile(data)


def test_duplicate_link_ids_are_errors():
    data = example_data()
    data["use_cases"][0]["questionnaire_response"]["items"]["pain_location"]["link_id"] = "2.1"
    with pytest.raises(ProfileError) as e:
        parse_profile(data)
    assert "2.1" in str(e.value)


def test_transcript_needs_the_phi_acknowledgement():
    with pytest.raises(ProfileError) as e:
        profile_with(uc__transcript={"enabled": True})
    assert "phi_acknowledged" in str(e.value)
    p = profile_with(uc__transcript={"enabled": True, "phi_acknowledged": True})
    assert p.use_cases[0].transcript.enabled


def test_unknown_tiers_in_the_priority_map_are_errors():
    with pytest.raises(ProfileError):
        profile_with(task__priority={"urgentish": "urgent"})
    with pytest.raises(ProfileError):
        profile_with(task__priority={"emergency": "now"})


def test_unknown_use_case_is_an_error():
    errs = errors(profile_with(uc__campaign_id="knee_clinic"))
    assert any("unknown use case" in e.message for e in errs)


def test_mappings_for_the_same_versions_overlap():
    data = example_data()
    data["use_cases"].append({**data["use_cases"][0]})
    with pytest.raises(ProfileError):
        parse_profile(data)
    data["use_cases"][1] = {**data["use_cases"][0], "spec_versions": [2]}
    data["use_cases"][0] = {**data["use_cases"][0], "spec_versions": [1]}
    p = parse_profile(data)
    assert p.for_use_case("post_op", 2).spec_versions == [2]
    assert p.for_use_case("post_op", 1).spec_versions == [1]
    assert p.for_use_case("post_op", 3) is None
    assert p.for_use_case("pre_op", 1) is None


def test_a_listed_version_beats_the_catch_all():
    data = example_data()
    data["use_cases"].append({**data["use_cases"][0], "spec_versions": [1], "observations": []})
    p = parse_profile(data)
    assert p.for_use_case("post_op", 1).observations == []
    assert p.for_use_case("post_op", 7).observations


def test_code_maps_that_miss_an_answer_warn():
    data = example_data()
    del data["use_cases"][0]["questionnaire_response"]["items"]["pain_trend"]["codes"]["same"]
    warnings = [p for p in profile_problems(parse_profile(data)) if not p.is_error]
    assert any("no code for answers ['same']" in w.message for w in warnings)


def test_engine_fields_can_be_mapped():
    p = example_profile()
    assert not errors(p)
    assert "patient_questions" in p.use_cases[0].questionnaire_response.items
