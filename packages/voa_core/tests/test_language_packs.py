"""Language packs (docs/LANGUAGES-PLAN.md §3.2, step 4): the format, the lint,
availability per channel, the draft gate and the missed-call text gate.
Fictional names only."""

from __future__ import annotations

import pytest
import yaml
from voa_core import language_packs as lp
from voa_core import languages, lifecycle
from voa_core.campaign import default_config_dir, load_campaign

I18N = default_config_dir() / "campaigns" / "i18n"
POST_OP = load_campaign("post_op")


def draft(campaign="post_op") -> dict:
    return yaml.safe_load((I18N / f"{campaign}.es-US.yaml").read_text())


def approved(campaign="post_op", **over) -> dict:
    doc = draft(campaign)
    doc.update(
        status="approved",
        reviewed_by="Test Translator",
        approved_by="Test Approver",
        approved_at="2026-10-22",
    )
    doc.update(over)
    return doc


def write(tmp_path, doc, name="post_op.es-US.yaml"):
    d = tmp_path / "campaigns" / "i18n"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc, allow_unicode=True))
    return tmp_path


# --------------------------------------------------------------------------- the real packs


@pytest.mark.parametrize(("cid", "lang", "path"), lp.pack_files())
def test_every_pack_in_the_repo_lints_clean(cid, lang, path):
    doc = yaml.safe_load(path.read_text())
    assert lp.pack_problems(doc, load_campaign(cid), file_campaign=cid, file_language=lang) == []


def test_the_spanish_post_op_pack_is_a_machine_draft():
    p = lp.load_pack("post_op", "es-US")
    assert p is not None and p.status == "draft" and not p.approved
    assert p.translated_by == "machine draft (Claude)"
    assert "qualified medical translator" in p.notes and "1557" in p.notes
    assert not (p.reviewed_by or p.approved_by or p.approved_at)
    # Wording only, complete for voice: every script and question.
    assert set(p.scripts) == set(POST_OP.scripts)
    assert set(p.questions) == set(POST_OP.question_ids)
    assert lp.voice_complete(p, POST_OP)
    # The clinical meaning kept: 911, the fever threshold, the medication list.
    assert "nueve uno uno" in p.scripts["emergency"] and "cuelgue" in p.scripts["emergency"]
    assert "cien punto cuatro" in p.questions["fever_reported"].ask
    assert "lista" in p.scripts["closing_extra"] and "medicamentos" in p.scripts["closing_extra"]
    assert "usted" in p.scripts["identity_failed"]


def test_no_patient_is_called_in_spanish_while_the_pack_is_a_draft(monkeypatch):
    assert languages.available("post_op") == {"en-US", "en-IN"}
    assert languages.available("post_op", channel="sms") == {"en-US", "en-IN"}
    assert languages.hold_reason("es-US", "post_op") == "Waiting for Spanish (US) support"
    # The eval switch never changes who may be dialled.
    monkeypatch.setenv("VOA_ALLOW_DRAFT_PACKS", "1")
    monkeypatch.setenv("VOA_ENV", "dev")
    assert languages.available("post_op") == {"en-US", "en-IN"}
    assert languages.hold_reason("es-US", "post_op") == "Waiting for Spanish (US) support"


# --------------------------------------------------------------------------- lint


def test_an_approved_pack_needs_its_reviewer_and_approver():
    doc = {**draft(), "status": "approved"}
    problems = lp.pack_problems(doc, POST_OP)
    assert "reviewed_by: required for status approved" in problems
    assert "approved_by: required for status approved" in problems
    assert "approved_at: required for status approved" in problems
    assert lp.pack_problems(approved(), POST_OP) == []
    assert "approved_at: must be a date, YYYY-MM-DD" in lp.pack_problems(
        approved(approved_at="last week"), POST_OP
    )


def test_every_voice_line_and_question_must_be_translated():
    doc = draft()
    del doc["scripts"]["voicemail"]
    del doc["questions"]["pain_trend"]
    doc["questions"]["sleeping_well"]["ask"] = ""
    problems = lp.pack_problems(doc, POST_OP)
    assert "scripts.voicemail: missing (the voice call says it)" in problems
    assert "questions.pain_trend.ask: missing (the voice agent asks it)" in problems
    assert "questions.sleeping_well.ask: missing (the voice agent asks it)" in problems


def test_placeholders_must_match_the_english():
    doc = draft()
    doc["scripts"]["opener"] = "Hola, habla {org_name}. ¿Podría hablar con {patient_first_name}?"
    doc["scripts"]["voicemail"] = "Hola {patient_first_name}, le llama {org}."
    problems = lp.pack_problems(doc, POST_OP)
    assert any(p.startswith("scripts.opener: placeholders") for p in problems)
    assert any(p.startswith("scripts.voicemail: placeholders") for p in problems)
    doc["scripts"]["consent"] = "Le llamo de {org_name"
    assert any("scripts.consent: bad braces" in p for p in lp.pack_problems(doc, POST_OP))


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Eso suena grave. Por favor, cuelgue ahora y llame al nueve uno uno.", []),
        ("Eso suena grave. Cuelgue y llame al 911.", []),
        ("Eso suena grave. Por favor, cuelgue y llame a emergencias.", ["must say 911"]),
        ("Eso suena grave. Llame al nueve uno uno.", ["hang up now and call"]),
    ],
)
def test_the_emergency_line_keeps_911_and_hang_up_and_call(text, want):
    problems = lp.emergency_problems(text, "es-US")
    assert len(problems) == len(want)
    for w, p in zip(want, problems, strict=True):
        assert w in p


def test_a_language_with_no_emergency_check_cannot_pass():
    (problem,) = lp.emergency_problems("Gọi 911 ngay.", "vi-VN")
    assert "EMERGENCY_WORDS" in problem


def test_no_extra_keys():
    doc = draft()
    doc["rules"] = [{"when": "pain_severity >= 2", "tier": "informational"}]
    doc["scripts"]["goodbye_song"] = "Adiós"
    doc["questions"]["shoe_size"] = {"ask": "¿Qué talla calza?"}
    doc["questions"]["overall_feeling"]["note"] = "an extra note"  # the English has none
    problems = lp.pack_problems(doc, POST_OP)
    assert any(p.startswith("rules: unknown key") for p in problems)
    assert "scripts.goodbye_song: not in the English spec (nothing says it)" in problems
    assert "questions.shoe_size: not a question of post_op" in problems
    assert "questions.overall_feeling.note: the English question has no note" in problems


def test_a_pack_cannot_change_a_type(tmp_path):
    doc = approved()
    doc["questions"]["pain_present"]["type"] = "scale"
    problems = lp.pack_problems(doc, POST_OP)
    assert any(p.startswith("questions.pain_present.type") for p in problems)
    write(tmp_path, doc)
    assert lp.load_pack("post_op", "es-US", tmp_path) is None  # never used
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN"}


def test_file_name_and_language_must_agree():
    problems = lp.pack_problems(draft(), POST_OP, file_campaign="pre_op", file_language="es-MX")
    assert any(p.startswith("language:") for p in problems)
    assert any(p.startswith("campaign:") for p in problems)
    assert any(
        "English uses the spec" in p
        for p in lp.pack_problems({**draft(), "language": "en-US"}, POST_OP)
    )


def test_sms_wording_is_optional_but_checked_when_present():
    doc = draft()
    doc["sms_scripts"] = {"preamble": "Gracias. Le enviaré {count} preguntas."}
    doc["questions"]["pain_present"]["sms"] = "¿Tiene dolor? Responda SÍ o NO."
    problems = lp.pack_problems(doc, POST_OP)
    assert any(p.startswith("sms_scripts.preamble: placeholders") for p in problems)
    doc["sms_scripts"] = {"preamble": "Gracias. Le enviaré {total} preguntas."}
    assert lp.pack_problems(doc, POST_OP) == []


# --------------------------------------------------------------------------- availability


def test_an_approved_voice_pack_is_available_for_voice_not_for_texts(tmp_path):
    write(tmp_path, approved())
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN", "es-US"}
    assert languages.available("post_op", tmp_path, channel="voice") == {"en-US", "en-IN", "es-US"}
    assert languages.available("post_op", tmp_path, channel="sms") == {"en-US", "en-IN"}
    assert languages.hold_reason("es-US", "post_op", config_dir=tmp_path) is None
    assert languages.text_block("es-US", "post_op", config_dir=tmp_path) == (
        "no approved Spanish (US) text wording"
    )


@pytest.mark.parametrize(
    "broken",
    [
        lambda d: d["scripts"].pop("emergency"),
        lambda d: d["questions"].pop("fever_reported"),
        lambda d: d["scripts"].update(opener="Hola, ¿hablo con {patient_first_name}?"),
        lambda d: d.update(approved_by=""),
        lambda d: d.update(language="es-MX"),
        lambda d: d.update(status="retired"),
        lambda d: d.update(status="in_review"),
    ],
)
def test_an_incomplete_or_unapproved_pack_keeps_the_patient_held(tmp_path, broken):
    doc = approved()
    broken(doc)
    write(tmp_path, doc)
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN"}
    assert languages.hold_reason("es-US", "post_op", config_dir=tmp_path)


def test_summary_rows_for_the_console():
    rows = {(r["campaign_id"], r["language"]): r for r in lp.summary()}
    es = rows[("post_op", "es-US")]
    assert es["status"] == "draft" and es["needs"] == "needs translator review"
    assert es["voice"] == {"complete": True, "available": False}
    assert es["sms"] == {"complete": False, "available": False}
    assert es["name"] == "Spanish (US)" and es["problems"] == []


# --------------------------------------------------------------------------- the gate


def test_localize_changes_wording_only():
    p = lp.load_pack("post_op", "es-US")
    es = lp.localize(POST_OP, p)
    assert es.scripts["opener"].startswith("Hola, esta es una llamada automatizada")
    assert es.question("pain_present").ask == "¿Tiene algún dolor?"
    assert es.question("pain_severity").note == "For a range, record the higher number."
    # Ids, types, rules, red flags and tests are the English spec's.
    assert es.question_ids == POST_OP.question_ids
    assert [q.type for q in es.questions] == [q.type for q in POST_OP.questions]
    assert es.rules == POST_OP.rules and es.all_red_flags == POST_OP.all_red_flags
    assert es.sms_scripts == POST_OP.sms_scripts
    assert POST_OP.scripts["opener"].startswith("Hello")  # the cached spec is untouched


@pytest.mark.parametrize(
    ("env", "allowed"),
    [
        ({"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "dev"}, True),
        ({"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "test"}, True),
        ({"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "demo"}, False),
        ({"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "prod"}, False),
        ({"VOA_ALLOW_DRAFT_PACKS": "1"}, False),  # VOA_ENV must be explicit
        ({"VOA_ALLOW_DRAFT_PACKS": "true", "VOA_ENV": "dev"}, False),
        ({"VOA_ENV": "dev"}, False),
    ],
)
def test_draft_packs_only_in_dev_or_test_evals(env, allowed):
    assert lp.drafts_allowed(env) is allowed


def test_the_spoken_language_gate(tmp_path):
    dev = {"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "test"}
    demo = {"VOA_ALLOW_DRAFT_PACKS": "1", "VOA_ENV": "demo"}
    # The repo's draft: English unless the eval switch is on in dev/test.
    assert lp.voice_pack_for_call("post_op", "es-US", environ={}) is None
    assert lp.voice_pack_for_call("post_op", "es-US", environ=demo) is None
    assert lp.voice_pack_for_call("post_op", "es-US", environ=dev).language == "es-US"
    assert lp.voice_pack_for_call("post_op", "es-US", True, environ=dev) is None
    assert lp.voice_pack_for_call("post_op", "en-US", environ=dev) is None
    assert lp.voice_pack_for_call("post_op", "vi-VN", environ=dev) is None  # no pack
    assert lp.voice_pack_for_call("pre_op", "es", environ=dev).campaign == "pre_op"
    # Approved: spoken in Spanish with no switch, unless "call in English".
    write(tmp_path, approved())
    assert lp.voice_pack_for_call("post_op", "es-US", config_dir=tmp_path, environ={})
    assert lp.voice_pack_for_call("post_op", "es-US", "true", config_dir=tmp_path) is None
    # Retired is never spoken, even in evals.
    write(tmp_path, approved(status="retired"))
    assert lp.voice_pack_for_call("post_op", "es-US", config_dir=tmp_path, environ=dev) is None


# --------------------------------------------------------------------------- texts


@pytest.mark.parametrize(
    ("doc", "want"),
    [
        ({"language": "es-US", "call_in_english": False}, "no approved Spanish (US) text wording"),
        ({"patient_context": {"language": "es-US"}}, "no approved Spanish (US) text wording"),
        ({"language": "es-US", "call_in_english": True}, None),
        ({"language": "en-US"}, None),
        ({"language": "en-IN"}, None),
        ({}, None),  # calls from before languages: English
        (
            {"language": "es-US", "outcome": "voicemail_left"},
            "no approved Spanish (US) text wording",
        ),
        # A person answered: that reason wins, as before.
        ({"language": "es-US", "outcome": "unable_to_reach_wrong_person"}, "wrong person answered"),
    ],
)
def test_the_missed_call_text_is_never_english_for_a_spanish_patient(doc, want):
    assert lifecycle.missed_call_text_block(doc) == want


def test_text_block():
    assert languages.text_block("es-US", "post_op") == "no approved Spanish (US) text wording"
    assert languages.text_block("vi-VN", "post_op") == "no approved Vietnamese text wording"
    assert languages.text_block("es-US", "post_op", True) is None
    assert languages.text_block(None, "post_op") is None
    assert languages.text_block("en-US", None) is None
