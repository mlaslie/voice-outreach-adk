"""Texts in a patient's language (docs/LANGUAGES-PLAN.md §3.5, step 5): the shared
SMS copy in the pack format, the encoding-aware lint, availability for texts and
the conversation in Spanish. Fictional names only."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
import yaml
from voa_core import copy as messages
from voa_core import language_packs as lp
from voa_core import languages, sms_copy, timeparse
from voa_core.campaign import default_config_dir, load_campaign
from voa_core.copy import SiteCopy
from voa_core.sms import conversation
from voa_core.sms import states as st
from voa_core.sms.conversation import ConversationPolicy, decide

I18N = default_config_dir() / "campaigns" / "i18n"
POST_OP = load_campaign("post_op")
TZ = ZoneInfo("America/New_York")


def draft() -> dict:
    return yaml.safe_load((I18N / "post_op.es-US.yaml").read_text())


def approved(**over) -> dict:
    doc = draft()
    doc.update(
        status="approved",
        reviewed_by="Test Translator",
        approved_by="Test Approver",
        approved_at="2026-10-22",
    )
    doc.update(over)
    return doc


def write(tmp_path, doc):
    d = tmp_path / "campaigns" / "i18n"
    d.mkdir(parents=True, exist_ok=True)
    (d / "post_op.es-US.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    return tmp_path


def pack(doc=None) -> lp.Pack:
    return lp._model(doc or draft())


# --------------------------------------------------------------------------- the keys


SITES = [
    SiteCopy(org_name="Acme Health", support_email="help@example.org"),
    SiteCopy(
        org_name="Acme Health",
        support_email="help@example.org",
        voice_number="(202) 555-0100",
        survey_enabled=True,
    ),
]


WHEN = "Thu Aug 13, 3:00 PM"


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name", ["Jordan Rivera", "", None])
def test_the_english_keys_rebuild_copy_py_byte_for_byte(site, name):
    """The pack format cannot drift from what English patients receive."""
    m = sms_copy.PackCopy(sms_copy.ENGLISH, None, approved=True)
    e = sms_copy.ENGLISH_COPY
    for got, want in [
        (m.invite(name, site), messages.invite(name, site)),
        (m.unclear_choice(site), messages.unclear_choice(site)),
        (m.calling_now(site), messages.calling_now(site)),
        (m.booked(WHEN, True, site), messages.booked(WHEN, True, site)),
        (m.booked(WHEN, False, site), messages.booked(WHEN, False, site)),
        (m.already_booked("x"), messages.already_booked("x")),
        (m.booking_canceled(site), messages.booking_canceled(site)),
        (m.opted_out(site), messages.opted_out(site)),
        (m.help_message(site), messages.help_message(site)),
        (m.session_closed(site), messages.session_closed(site)),
        (m.ask_dob(name), messages.ask_dob(name)),
        (m.confirm_time_prompt("Fri", True), messages.confirm_time_prompt("Fri", True)),
        (m.confirm_time_prompt("Fri", False), messages.confirm_time_prompt("Fri", False)),
    ]:  # fmt: skip
        assert got == want
    for fn in (
        "gave_up_choice",
        "cannot_call_back",
        "ask_for_time",
        "unclear_time",
        "gave_up_time",
        "nothing_to_cancel",
        "emergency",
        "dob_unreadable",
        "dob_failed",
        "survey_stopped",
        "your_callback",
    ):
        assert getattr(m, fn)() == getattr(e, fn)(), fn
    assert e.your_callback() == "your callback"


@pytest.mark.parametrize(
    "when",
    [
        datetime.datetime(2026, 8, 13, 15, 0, tzinfo=TZ),
        datetime.datetime(2027, 1, 5, 9, 30, tzinfo=TZ),
        datetime.datetime(2026, 3, 1, 12, 0, tzinfo=TZ),
    ],
)
def test_the_english_time_format_rebuilds_humanize(when):
    now = datetime.datetime(2026, 3, 3, 10, 0, tzinfo=TZ)
    m = sms_copy.PackCopy(sms_copy.ENGLISH, None, approved=True)
    assert m.humanize(when, now) == timeparse.humanize(when, now)


def test_spanish_time_format():
    m = sms_copy.PackCopy(pack().sms_copy, "es-US", approved=False)
    now = datetime.datetime(2026, 3, 3, 10, 0, tzinfo=TZ)
    assert m.humanize(datetime.datetime(2026, 3, 4, 15, 0, tzinfo=TZ), now) == "mié 4 mar, 3:00 PM"
    assert m.humanize(datetime.datetime(2027, 1, 3, 9, 0, tzinfo=TZ), now) == (
        "dom 3 ene 2027, 9:00 AM"
    )


# --------------------------------------------------------------------------- encoding


@pytest.mark.parametrize(
    ("text", "enc", "units", "n"),
    [
        ("a" * 160, "GSM-7", 160, 1),
        ("a" * 161, "GSM-7", 161, 2),
        ("a" * 306, "GSM-7", 306, 2),
        ("a" * 307, "GSM-7", 307, 3),
        ("{" * 80, "GSM-7", 160, 1),  # extension characters cost two
        ("ñ¿¡é" * 10, "GSM-7", 40, 1),  # in GSM 03.38
        ("á" * 70, "UCS-2", 70, 1),
        ("á" * 71, "UCS-2", 71, 2),
        ("á" * 134, "UCS-2", 134, 2),
        ("á" * 135, "UCS-2", 135, 3),
        ("a" * 69 + "\U0001f600", "UCS-2", 71, 2),  # an emoji is two UTF-16 units
    ],
)
def test_segments(text, enc, units, n):
    seg = sms_copy.segments(text)
    assert (seg.encoding, seg.units, seg.segments) == (enc, units, n)


def test_every_english_shared_text_stays_gsm7():
    for key, text in sms_copy.ENGLISH.items():
        assert sms_copy.segments(text).encoding == "GSM-7", key


# --------------------------------------------------------------------------- the lint


def test_the_spanish_post_op_texts_are_complete_lint_clean_and_a_draft():
    p = lp.load_pack("post_op", "es-US")
    assert p is not None and p.status == "draft"
    assert set(p.sms_copy) == set(sms_copy.KEYS)
    assert lp.sms_problems(p, POST_OP) == []
    assert lp.sms_complete(p, POST_OP)
    rows = lp.sms_segment_report(p, POST_OP)
    assert rows and max(r["segments"] for r in rows) <= sms_copy.FAIL_ABOVE_SEGMENTS
    # Literal requirements of the wording itself.
    assert "Responda STOP para cancelar." in p.sms_copy["opt_out_line"]
    assert "DD/MM/AAAA" in p.sms_copy["ask_dob"]
    assert '"mañana 3pm"' in p.sms_copy["ask_for_time"]
    assert "usted" in p.sms_copy["you"]


@pytest.mark.parametrize(
    ("edit", "want"),
    [
        (lambda d: d["sms_copy"].pop("help"), "sms_copy.help: missing"),
        (lambda d: d["sms_copy"].update(extra="x"), "sms_copy.extra: not a shared SMS text"),
        (
            lambda d: d["sms_copy"].update(opted_out="Adiós de {organizacion}."),
            "sms_copy.opted_out: placeholders",
        ),
        (
            lambda d: d["sms_copy"].update(opt_out_line="Responda ALTO para cancelar."),
            "sms_copy.opt_out_line: must keep the English keyword STOP",
        ),
        (
            lambda d: d["sms_copy"].update(
                emergency="Llame al nueve uno uno si es una emergencia."
            ),
            "sms_copy.emergency: must keep 911 in digits",
        ),
        (
            lambda d: d["sms_copy"].update(emergency="Llame al 911 si es una emergencia."),
            "sms_copy.emergency: must keep the tap-to-call link tel:911",
        ),
        (
            lambda d: d["sms_copy"].update(booked_cancel="Responda CANCELAR para cancelar."),
            'sms_copy.booked_cancel: must keep the reply letter "C"',
        ),
        (
            lambda d: d["sms_copy"].update(menu_pick_time="dos = elegir una hora"),
            'sms_copy.menu_pick_time: must start with the digit "2"',
        ),
        (
            lambda d: d["sms_copy"].update(weekdays="lun mar mié"),
            "sms_copy.weekdays: must be 7 space-separated words",
        ),
        (
            lambda d: d["sms_copy"].update(
                ask_dob="Confirme la fecha de {name} en formato MM/DD/AAAA."
            ),
            "sms_copy.ask_dob: must ask for the date day first",
        ),
        (
            lambda d: d["sms_scripts"].pop("closing_urgent"),
            "sms_scripts.closing_urgent: missing",
        ),
        (
            lambda d: d["sms_scripts"].update(
                cut_short="Gracias. En una emergencia, llame al nueve uno uno."
            ),
            "sms_scripts.cut_short: must keep 911 in digits",
        ),
        (
            lambda d: d["questions"]["pain_present"].pop("sms"),
            "questions.pain_present.sms: missing",
        ),
        (
            lambda d: d["sms_scripts"].update(
                closing_extra="Por favor, lleve a su próxima cita de seguimiento en la clínica "
                "una lista completa y actualizada de todos los medicamentos, vitaminas y "
                "suplementos que está tomando en este momento."
            ),
            "sms_scripts.closing_routine: 5 segments (UCS-2",
        ),
    ],
)
def test_the_sms_lint(edit, want):
    doc = draft()
    edit(doc)
    problems = lp.sms_problems(pack(doc), POST_OP)
    assert any(p.startswith(want) for p in problems), problems
    assert not lp.sms_complete(pack(doc), POST_OP)


def test_three_segments_warn_four_fail():
    assert (sms_copy.WARN_ABOVE_SEGMENTS, sms_copy.FAIL_ABOVE_SEGMENTS) == (2, 3)
    warnings = lp.sms_warnings(pack(), POST_OP)
    assert warnings and all("3 segments" in w for w in warnings)


def test_an_sms_problem_never_touches_voice(tmp_path):
    doc = approved()
    doc["sms_copy"].pop("help")
    write(tmp_path, doc)
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN", "es-US"}
    assert languages.available("post_op", tmp_path, channel="sms") == {"en-US", "en-IN"}


# --------------------------------------------------------------------------- availability


def test_texts_are_available_only_with_an_approved_complete_pack(tmp_path):
    assert languages.available("post_op", channel="sms") == {"en-US", "en-IN"}  # the draft
    write(tmp_path, approved())
    assert languages.available("post_op", tmp_path, channel="sms") == {"en-US", "en-IN", "es-US"}
    assert languages.text_block("es-US", "post_op", config_dir=tmp_path) is None
    assert languages.text_language("es-US", "post_op", config_dir=tmp_path) == "es-US"
    # "Call in English": English applies, as today.
    assert languages.text_language("es-US", "post_op", True, tmp_path) is None
    assert languages.text_language("en-US", "post_op", config_dir=tmp_path) is None
    # The draft in the repo: no texts in Spanish.
    assert languages.text_language("es-US", "post_op") is None
    assert languages.text_block("es-US", "post_op") == "no approved Spanish (US) text wording"


def test_drafts_only_with_allow_drafts(tmp_path):
    assert lp.sms_pack("post_op", "es-US") is None
    assert lp.sms_pack("post_op", "es-US", allow_drafts=True) is not None
    assert lp.sms_pack("post_op", "en-US", allow_drafts=True) is None
    doc = approved()
    write(tmp_path, doc)
    assert lp.sms_pack("post_op", "es-US", config_dir=tmp_path).approved
    doc["status"] = "retired"
    write(tmp_path, doc)
    assert lp.sms_pack("post_op", "es-US", allow_drafts=True, config_dir=tmp_path) is None


def test_localize_sms_changes_sms_wording_only():
    c = lp.localize_sms(POST_OP, pack())
    q = c.question("pain_present")
    assert q.sms.startswith("¿Tiene algún dolor") and q.ask == POST_OP.question("pain_present").ask
    assert c.sms_scripts["preamble"].startswith("Gracias")
    assert c.rules == POST_OP.rules and c.question_ids == POST_OP.question_ids
    assert c.scripts == POST_OP.scripts


# --------------------------------------------------------------------------- the machine


NOW = datetime.datetime(2026, 3, 3, 10, 0, tzinfo=TZ)
SITE = SiteCopy(org_name="Acme Health", support_email="help@example.org")
POLICY = ConversationPolicy(survey_enabled=True, reply_to_compliance_keywords=True)


def _session(state=st.STATE_AWAITING_CHOICE, **kw):
    return {
        "state": state,
        "sms_language": "es-US",
        "sms_draft_wording": True,  # the repo pack is a draft (as in the simulator)
        "patient_context": {
            "patient_name": "Lucía Prueba",
            "patient_id": "P-9",
            "patient_dob": "1950-03-15",
            "language": "es-US",
        },
        **kw,
    }


def _decide(session, body):
    return decide(session, body, NOW, site=SITE, policy=POLICY)


def test_the_machine_speaks_the_pack_and_english_stays_english():
    es = sms_copy.PackCopy(pack().sms_copy, "es-US", approved=False)
    d = _decide(_session(), "3")
    assert d.reply == es.ask_dob("Lucía Prueba")
    assert d.reply.startswith("Antes de empezar") and "DD/MM/AAAA" in d.reply
    assert _decide(_session(), "2").reply == es.ask_for_time()
    assert _decide(_session(), "1").reply == es.calling_now(SITE)
    assert _decide(_session(), "ALTO").reply == es.opted_out(SITE)
    assert _decide(_session(), "AYUDA").reply == es.help_message(SITE)
    assert "STOP" in _decide(_session(), "AYUDA").reply
    english = {k: v for k, v in _session().items() if not k.startswith("sms_")}
    assert _decide(english, "3").reply == messages.ask_dob("Lucía Prueba")


def test_a_spanish_booking_reads_back_in_spanish():
    d = _decide(_session(st.STATE_AWAITING_TIME), "tomorrow 3pm")
    assert d.reply == "Su llamada queda para el mié 4 mar, 3:00 PM. Responda C para cancelar."


def test_a_session_whose_pack_is_unusable_gets_english_objects_but_no_drafts():
    # Without the simulator flag, a draft pack is never used by the machine.
    s = _session()
    s.pop("sms_draft_wording")
    assert conversation.session_copy(s) is sms_copy.ENGLISH_COPY
    assert conversation.campaign_of(s).question("pain_present").sms == (
        POST_OP.question("pain_present").sms
    )
