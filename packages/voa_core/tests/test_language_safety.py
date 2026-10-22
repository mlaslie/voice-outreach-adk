"""The safety layer for other languages (docs/LANGUAGES-PLAN.md §3.3, step 3).

The first seven tests are the survey's known failures, each of which failed on
the code before this step. Fictional data only."""

from __future__ import annotations

import datetime

import pytest
from voa_core import checks, dob, intent, language_safety, parsers, review, translate
from voa_core import patient_questions as pq

TODAY = datetime.date(2026, 10, 22)


# --------------------------------------------------------------------------- the 7 known failures


def test_1_a_spanish_red_flag_is_caught():
    # Before: False (no 911 text, no emergency filing).
    assert intent.is_red_flag("tengo dolor en el pecho y no puedo respirar")
    assert intent.red_flag_match("Me desmayé y estoy sangrando mucho") is not None
    # Negated, in the same clause: the patient says what they do NOT have.
    assert not intent.is_red_flag("no tengo dolor en el pecho")
    assert not intent.is_red_flag("Estoy bien, sin dolor de pecho")
    # A denial does not hide a flag in the next clause.
    assert intent.is_red_flag("No tengo fiebre, pero me duele el pecho")


def test_2_dont_know_is_never_no_and_si_is_never_same():
    # Before: "no" and "same".
    assert parsers.parse_yes_no("no sé") is None
    assert parsers.parse_yes_no("no se") is None
    assert parsers.parse_yes_no("tal vez") is None
    assert parsers.parse_trend("sí") is None
    assert parsers.parse_scale("siete") is None
    # _norm keeps letters of every script (it used to delete "í").
    assert parsers._norm("sí") == "sí"
    # English unchanged.
    assert parsers.parse_yes_no("no") == "no"
    assert parsers.parse_yes_no("I don't know") is None
    assert parsers.parse_trend("s") == "same"
    assert parsers.parse_scale("about a 4") == 4


def test_3_free_text_checks_fail_safe_on_other_languages():
    # Before: all False.
    assert checks.swelling_is_urgent("solo la pierna izquierda, está peor")
    assert checks.severe_language("me duele muchísimo")
    assert checks.asks_for_a_person("quiero hablar con alguien")
    assert checks.expressed_worry("estoy preocupada")
    # English unchanged.
    assert not checks.swelling_is_urgent("both ankles a little, not painful")
    assert not checks.severe_language("it is fine")


def test_3b_the_prose_rules_add_a_finding_for_words_they_cannot_read():
    from voa_core import survey

    answers = {
        "overall_feeling": "más o menos, preocupada",
        "pain_present": "no",
        "fever_reported": "no",
        "incision_concerns": "no",
        "legs_swollen": "no",
        "sleeping_well": "yes",
        "has_questions": "no",
    }
    t = survey.triage(answers)
    assert t.follow_up == "urgent_same_day"
    texts = [f["text"] for f in t.findings]
    assert "answer in another language (may express worry)" in texts
    # The same answers in English: nothing.
    english = {**answers, "overall_feeling": "pretty good"}
    assert survey.triage(english).follow_up == "none"


def test_4_a_spanish_question_is_never_routed_by_an_english_stem():
    # Before: diet, next business day.
    c = pq.classify("¿Qué dieta debo seguir si tengo fiebre y sangrado?")
    assert (c.category, c.priority) == ("other", "urgent_same_day")
    # Unaccented too ("que", "si" are not English).
    assert pq.classify("que dieta debo seguir si tengo fiebre").priority == "urgent_same_day"
    # English unchanged.
    assert pq.classify("What should I eat?").priority == "routine_next_business_day"


def test_4b_a_non_latin_text_question_is_recorded_not_dropped():
    from voa_core.sms.conversation import _read_questions_reply

    # Before: (None, "") -> dropped after the re-ask.
    kind, text = _read_questions_reply("Когда я смогу водить машину")
    assert kind == "question" and text.startswith("Когда")
    assert _read_questions_reply("no") == ("no", "")


def test_5b_knowledge_content_must_be_english():
    from voa_core import knowledge as kb

    with pytest.raises(ValueError, match="language packs"):
        kb.clean_fields({"question": "¿Cuándo puedo ducharme?", "answer": "Después de 48 horas."})
    with pytest.raises(ValueError, match="language packs"):
        kb.clean_document_fields({"title": "Cuidado de la herida", "body": "Mantenga seco."})
    # A Spanish medication item saved earlier cannot be approved.
    with pytest.raises(ValueError, match="language packs"):
        kb.approve_item(
            {"status": "draft", "question": "¿Puedo tomar ibuprofeno?", "answer": "Sí."},
            by="a",
            at="t",
        )
    assert kb.clean_fields({"question": "When can I shower?", "answer": "After 48 hours."})
    assert kb.clean_document_fields({"title": "Dose", "body": "Up to 500 µg."})


def test_6_day_first_dates_for_other_languages():
    # Before: "05/03/1950" was 3 May (identity failure for a day-first speaker).
    assert dob.parse_dob("05/03/1950", TODAY) == datetime.date(1950, 5, 3)  # English: unchanged
    assert dob.parse_dob("05/03/1950", TODAY, day_first=True) == datetime.date(1950, 3, 5)
    assert dob.parse_dob("15/03/1950", TODAY, day_first=True) == datetime.date(1950, 3, 15)
    assert dob.parse_dob("15031950", TODAY, day_first=True) == datetime.date(1950, 3, 15)
    assert dob.parse_dob("1950-03-15", TODAY, day_first=True) == datetime.date(1950, 3, 15)
    assert dob.ambiguous_numeric("05/03/1950")
    assert not dob.ambiguous_numeric("15/03/1950")
    assert not dob.ambiguous_numeric("05/05/1950")
    assert not dob.ambiguous_numeric("March 5 1950")


def test_6b_a_spoken_date_in_another_language_reads_through_its_translation():
    # Before: "15 de marzo de 1950" was unparseable, re-asked without limit.
    assert dob.parse_dob("15 de marzo de 1950", TODAY) is None
    assert dob.parse_translated("15 de marzo de 1950", "March 15, 1950", TODAY) == datetime.date(
        1950, 3, 15
    )
    assert dob.parse_translated(
        "cinco de marzo de mil novecientos cincuenta", "March fifth, nineteen fifty", TODAY
    ) == datetime.date(1950, 3, 5)
    assert dob.spoken_numbers_to_digits("the twenty-first of June, two thousand three") == (
        "the 21 of june 2003"
    )


def test_7_stop_and_help_in_other_languages():
    # Before: None.
    for word in ("ALTO", "PARAR", "Cancelar", "baja", "ARRÊT", "arret", "СТОП", "إيقاف", "중지"):
        assert intent.classify_compliance(word) is intent.Intent.STOP, word
    for word in ("AYUDA", "Hilfe", "помощь", "मदद"):
        assert intent.classify_compliance(word) is intent.Intent.HELP, word
    # Whole message only, like carriers.
    assert intent.classify_compliance("alto el dolor") is None
    # English unchanged.
    assert intent.classify_compliance("STOP") is intent.Intent.STOP
    assert intent.classify_compliance("START") is intent.Intent.START
    assert intent.classify_compliance("1") is None


# --------------------------------------------------------------------------- keyword file


def test_every_language_has_stop_and_help_lists_marked_draft():
    import yaml
    from voa_core import languages
    from voa_core.campaign import default_config_dir

    doc = yaml.safe_load((default_config_dir() / intent.KEYWORDS_FILE).read_text())
    assert doc["status"] == "draft" and "translator" in doc["review"]
    assert set(doc["stop_help"]) == set(languages.CODES)
    for code, entry in doc["stop_help"].items():
        assert entry["status"] == "draft" and "translator" in entry["review"], code
        if code not in languages.ENGLISH:
            assert entry["stop"] and entry["help"], code
    assert doc["red_flags"]["es-US"]["status"] == "draft"


def test_no_extra_keyword_collides_with_an_english_reply():
    from voa_core.sms.conversation import _SIGN_OFF

    kw = intent.keywords()
    english_replies = (
        set(parsers._YES)
        | set(parsers._NO)
        | set(parsers._TREND)
        | {"1", "2", "3", "c", "ok", "okay", "later", "now", "text", "call", "yes", "no"}
        | set(_SIGN_OFF)
    )
    assert not (kw.stop | kw.help) & english_replies


def test_translated_red_flag_phrasings_only_for_translations():
    # A translator's English for "perdí el conocimiento".
    assert intent.is_red_flag("I lost consciousness", translated=True)
    assert intent.is_red_flag("Severe bleeding from the wound", translated=True)
    assert not intent.is_red_flag("I did not lose consciousness", translated=True)
    # An English patient's own words are read exactly as before.
    assert not intent.is_red_flag("I lost consciousness")


def test_looks_english_is_a_hint():
    assert not translate.looks_english("dolor de pecho")
    assert not translate.looks_english("no se")
    assert translate.looks_english("no")
    assert translate.looks_english("I don't know")


# --------------------------------------------------------------------------- the re-check


def _call(**kw):
    return {
        "follow_up": "routine_next_business_day",
        "follow_up_reason": "Patient reports pain 5/10.",
        "follow_up_reasons": [
            {"tier": "routine_next_business_day", "text": "pain 5/10", "source": "triage"}
        ],
        **kw,
    }


def test_findings_on_translated_text():
    f = language_safety.english_findings("I have chest pain", "translated transcript")
    assert [(x.tier, x.kind) for x in f] == [("emergency", "red_flag")]
    assert f[0].text == "red flag found on translated transcript: chest pain"
    f = language_safety.english_findings("only the left leg is swollen", "translated transcript")
    assert ("urgent_same_day", "swelling") in [(x.tier, x.kind) for x in f]
    assert language_safety.english_findings("I'm not worried, it's not terrible", "x") == []


def test_a_raise_adds_a_reason_and_never_lowers():
    finding = language_safety.Finding("emergency", "red_flag", "red flag found: chest pain", "t")
    doc, withdraw = language_safety.apply(_call(), [finding], now="2026-10-22T10:00:00Z")
    assert doc["follow_up"] == "emergency" and not withdraw
    assert doc["follow_up_reasons"][0] == {
        "tier": "emergency",
        "text": "red flag found: chest pain",
        "source": "translation_check",
        "fields": [],
        "at": "2026-10-22T10:00:00Z",
    }
    assert doc["follow_up_reasons"][1]["text"] == "pain 5/10"  # kept
    assert doc["follow_up_reason"].startswith("Found on the translated transcript")
    assert doc["follow_up_history"][-1]["by"] == "translation_check"
    assert doc["translation_check"]["raised_to"] == "emergency"

    # A milder finding on a more urgent call: recorded, nothing lowered.
    mild = language_safety.Finding("urgent_same_day", "expressed_worry", "worry", "t")
    doc2, _ = language_safety.apply(_call(follow_up="emergency"), [mild], now="t")
    assert doc2["follow_up"] == "emergency"
    assert doc2["translation_check"]["raised_to"] is None
    assert doc2["translation_check"]["findings"][0]["kind"] == "expressed_worry"
    assert doc2["follow_up_reasons"] == _call()["follow_up_reasons"]


def test_a_raise_reopens_a_signed_off_review_and_withdraws_its_export():
    finding = language_safety.Finding("urgent_same_day", "swelling", "swelling found", "t")
    signed = _call(review={"status": "signed_off", "by": "nurse@example.org"})
    doc, withdraw = language_safety.apply(signed, [finding], now="t")
    assert withdraw
    assert doc["review"]["status"] == "reopened"
    assert doc["review"]["reopened_by"] == "translation_check"
    assert doc["review"]["export_withdrawn"] is True
    assert doc["translation_check"]["reopened"] is True
    assert review.is_open_follow_up(doc)


def test_a_raised_tier_is_held_against_a_milder_late_report():
    finding = language_safety.Finding("emergency", "red_flag", "red flag", "t")
    doc, _ = language_safety.apply(_call(), [finding], now="t")
    patch, _ = review.merge_late_report(doc, {"follow_up": "none"}, now="t2")
    assert "follow_up" not in patch  # the late "none" does not lower it


def test_translated_questions_keep_the_more_urgent_priority():
    qs = [
        {"text": "¿Puedo manejar?", "category": "other", "priority": "urgent_same_day"},
        {
            "text": "When can I drive?",
            "category": "activity",
            "priority": "routine_next_business_day",
        },
    ]
    out, found = language_safety.question_findings(qs, {0: "Can I drive?"})
    assert out[0]["priority"] == "urgent_same_day"  # kept, never lowered
    assert out[0]["category"] == "activity" and out[0]["text_en"] == "Can I drive?"
    assert out[0]["text_original"] == "¿Puedo manejar?"
    assert found == []
    out, found = language_safety.question_findings(
        [{"text": "x", "category": "diet", "priority": "routine_next_business_day"}],
        {0: "What should I eat if I have a fever?"},
    )
    assert out[0]["priority"] == "urgent_same_day"
    assert found[0].kind == "question"
