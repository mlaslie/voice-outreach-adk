"""Generate the post-op PARITY goldens (docs/USE-CASES-PLAN.md, step 1).

Run ONCE against the pre-refactor code; the JSON it writes under ``golden/`` is
checked in and ``test_parity.py`` replays every input against the current code.
After the generic-engine refactor these files must NOT be regenerated: a
difference is a behaviour change, which for post-op is a bug.

    uv run python packages/voa_core/tests/parity/generate_parity.py

Every golden stores its inputs next to the outputs, so the test never depends on
this script (or on the random corpus) staying the same. Synthetic data only.

Categories:
  (a) triage_N.json          survey.triage over a synthetic corpus + every test input
  (b) reports.json           reports.validate_report errors / clean payloads
  (c) instruction.json       the voice instruction for several patients
  (d) tool_declarations.json the voice tool declarations
  (e) sms_texts.json         every SMS text and variant; sms_conversations_*.json
                             scripted conversations and every captured decide() call
  (f) campaign_sequences.json applicable()/missing()/build_plan/followups_for
  (g) voice_tools.json       scripted record_answer / submit_report sessions
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import itertools
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
CAPTURED = GOLDEN / "captured_inputs.json"
TRIAGE_CHUNK = 600  # files stay under the repo's 1 MB limit

# --------------------------------------------------------------------------- helpers


def _dump(name: str, data) -> None:
    GOLDEN.mkdir(exist_ok=True)
    if isinstance(data, list):
        # One case per line: compact, and a diff points at the case.
        rows = (json.dumps(x, sort_keys=True, ensure_ascii=True) for x in data)
        text = "[\n" + ",\n".join(rows) + "\n]"
    else:
        text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True)
    (GOLDEN / name).write_text(text + "\n")
    n = len(data) if isinstance(data, list) else sum(len(v) for v in data.values())
    print(f"{name}: {n} cases")


def _rt(x):
    """JSON round trip: the test sees exactly what the golden stores."""
    return json.loads(json.dumps(x))


def triage_out(t) -> dict:
    return {
        "follow_up": t.follow_up,
        "reason": t.reason,
        "triggers": list(t.triggers),
        "findings": [dict(f) for f in t.findings],
    }


def decision_out(d) -> dict:
    return {
        "reply": d.reply,
        "state": d.state,
        "effect": str(d.effect),
        "when": d.when.isoformat() if d.when else None,
        "retry": d.retry,
        "note": d.note,
        "session_patch": d.session_patch,
        "extra_replies": list(d.extra_replies),
    }


# --------------------------------------------------------------------------- (a) triage


def _pq_entries():
    from voa_core import patient_questions as pq

    med = pq.record("how much tylenol can I take with my other pills")
    appt = pq.record("when is my follow-up appointment")
    drive = pq.record("when can I drive again")
    wound = pq.record("is it normal that my wound is leaking a little")
    answered = {**appt, "outcome": "answered"}
    answered_unsat = {**drive, "outcome": "answered", "satisfied": False}
    invalid = {"text": "", "category": "nope", "priority": "x"}
    return {
        "none": None,
        "empty": [],
        "med": [med],
        "appt": [appt],
        "mixed": [med, appt, drive],
        "wound_drive": [wound, drive],
        "answered": [answered],
        "answered_unsat": [answered_unsat],
        "invalid_only": [invalid],
        "not_a_list": "how much tylenol",
    }


TRIAGE_FACTORS: dict[str, list] = {
    "overall_feeling": [
        None,
        "",
        "pretty good",
        "the pain is terrible",
        "I'd like a nurse to call me",
        "I'm a bit worried about it",
        "my incision hurts",
        "fine, not worried",
        "really bad day, I want to talk to someone",
    ],
    "pain_present": [None, "yes", "no", "unknown", "Yes"],
    "pain_severity": [None, 0, 3, 5, 7, 8, 10, "7", 4.5, True],
    "pain_location": [None, "", "my knee", "excruciating pain in my hip", "back, it's awful"],
    "pain_trend": [None, "better", "same", "worse"],
    "fever_reported": [None, "yes", "no", "unknown"],
    "incision_concerns": [None, "yes", "no", "unknown"],
    "incision_description": [None, "", "  ", "red and warm", "some drainage and I'm scared"],
    "legs_swollen": [None, "yes", "no", "unknown"],
    "swelling_description": [
        None,
        "",
        "both legs, not getting worse",
        "just my left leg",
        "both, no redness",
        "getting bigger",
        "both feet, not painful",
        "both ankles, about the same",
        "right calf is tender",
        "both legs, a little puffy",
        "short of breath and swollen",
    ],
    "sleeping_well": [None, "yes", "no", "unknown"],
    "blood_sugar_status": [None, "high", "low", "in_range", "unknown", "120 to 250"],
    "has_questions": [None, "yes", "no"],
    "patient_questions": list(_pq_entries()),  # keys; resolved below
    "_raw": [
        None,
        {},
        {"pain_present": "yes, it's awful"},
        {"fever_reported": "no but please have a doctor call me"},
        {"has_questions": "can I talk to a nurse about my meds"},
        {"overall_feeling": "I'm nervous", "sleeping_well": "no"},
        {"legs_swollen": "yes my left leg", "pain_severity": "8 out of 10"},
        ["I'm anxious about the stitches"],
        ["fine"],
    ],
}


def _triage_corpus() -> list[dict]:
    pqs = _pq_entries()
    keys = list(TRIAGE_FACTORS)

    def build(choice: dict) -> dict:
        out = {}
        for k, v in choice.items():
            if k == "patient_questions":
                v = pqs[v or "none"]
            if v is not None:
                out[k] = copy.deepcopy(v)
        return out

    seen: set[str] = set()
    corpus: list[dict] = []

    def add(a: dict) -> None:
        s = json.dumps(a, sort_keys=True)
        if s not in seen:
            seen.add(s)
            corpus.append(a)

    # 1. One factor at a time, over a few baselines.
    baselines = [
        {k: None for k in keys},
        {
            **{k: None for k in keys},
            "overall_feeling": "pretty good",
            "pain_present": "no",
            "fever_reported": "no",
            "incision_concerns": "no",
            "legs_swollen": "no",
            "sleeping_well": "yes",
            "has_questions": "no",
        },
        {
            **{k: None for k in keys},
            "overall_feeling": "ok",
            "pain_present": "yes",
            "pain_severity": 5,
            "pain_location": "my knee",
            "pain_trend": "same",
            "fever_reported": "no",
            "incision_concerns": "yes",
            "incision_description": "red and warm",
            "legs_swollen": "yes",
            "swelling_description": "both legs, not getting worse",
            "sleeping_well": "no",
            "has_questions": "yes",
        },
    ]
    for base in baselines:
        add(build(base))
        for k in keys:
            for v in TRIAGE_FACTORS[k]:
                add(build({**base, k: v}))
    # 2. The interacting pairs, exhaustively, over the second baseline.
    pairs = [
        ("pain_present", "pain_severity"),
        ("pain_present", "pain_location"),
        ("pain_severity", "pain_trend"),
        ("incision_concerns", "incision_description"),
        ("legs_swollen", "swelling_description"),
        ("has_questions", "patient_questions"),
        ("pain_present", "_raw"),
        ("overall_feeling", "pain_present"),
        ("blood_sugar_status", "fever_reported"),
    ]
    for a, b in pairs:
        for va, vb in itertools.product(TRIAGE_FACTORS[a], TRIAGE_FACTORS[b]):
            add(build({**baselines[1], a: va, b: vb}))
    # 3. Random full combinations (seeded).
    rng = random.Random(20261021)  # noqa: S311 (a reproducible corpus, not security)
    for _ in range(3500):
        add(build({k: rng.choice(TRIAGE_FACTORS[k]) for k in keys}))
    return corpus


def gen_triage(captured: dict) -> list[dict]:
    from voa_core import survey

    corpus = _triage_corpus()
    seen = {json.dumps(a, sort_keys=True) for a in corpus}
    for a in captured.get("triage", []):
        s = json.dumps(a, sort_keys=True)
        if s not in seen:
            seen.add(s)
            corpus.append(a)
    return [{"answers": a, "out": _rt(triage_out(survey.triage(_rt(a))))} for a in _rt(corpus)]


# --------------------------------------------------------------------------- (b) reports

_COMPLETE = {
    "overall_feeling": "ok",
    "pain_present": "no",
    "fever_reported": "no",
    "incision_concerns": "no",
    "legs_swollen": "no",
    "sleeping_well": "yes",
    "has_questions": "no",
}


def _report_payloads() -> list[dict]:
    from voa_core import patient_questions as pq

    base = {
        "patient_id": "PT-1001",
        "follow_up": "none",
        "follow_up_reason": "No concerning symptoms reported.",
        "outcome": "questionnaire_completed",
        "survey_responses": dict(_COMPLETE),
    }
    pain_full = {"pain_severity": 6, "pain_location": "knee", "pain_trend": "same"}
    surveys: list = [
        dict(_COMPLETE),
        {},
        None,
        "not a dict",
        [],
        {"overall_feeling": "ok"},
        {**_COMPLETE, "pain_present": "yes"},
        {**_COMPLETE, "pain_present": "yes", "pain_severity": 6},
        {**_COMPLETE, "pain_present": "yes", "pain_severity": 6, "pain_location": "knee"},
        {**_COMPLETE, "pain_present": "yes", **pain_full},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_severity": "6"},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_severity": 11},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_severity": True},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_severity": -1},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_severity": 6.0},
        {**_COMPLETE, "pain_present": "yes", **pain_full, "pain_location": ""},
        {**_COMPLETE, "pain_present": "Yes"},
        {**_COMPLETE, "incision_concerns": "yes"},
        {**_COMPLETE, "incision_concerns": "yes", "incision_description": ""},
        {**_COMPLETE, "incision_concerns": "yes", "incision_description": "red"},
        {**_COMPLETE, "legs_swollen": "yes"},
        {**_COMPLETE, "legs_swollen": "yes", "swelling_description": "left leg"},
        {
            **_COMPLETE,
            "pain_present": "yes",
            "incision_concerns": "yes",
            "legs_swollen": "yes",
        },
        {**_COMPLETE, "patient_questions": [pq.record("can I shower")]},
        {**_COMPLETE, "patient_questions": "x"},
        {**_COMPLETE, "patient_questions": [pq.record("q")] * 6},
        {**_COMPLETE, "patient_questions": [{"text": "x"}]},
        {**_COMPLETE, "patient_questions": None},
        {**_COMPLETE, "blood_sugar_status": "high", "blood_sugar_reading": "250"},
        {**_COMPLETE, "extra_field": 1},
    ]
    for f in list(_COMPLETE):
        surveys.append({k: v for k, v in _COMPLETE.items() if k != f})
    out = []
    for outcome in ("questionnaire_completed", None, "voicemail_left", "bogus"):
        for s in surveys:
            p = {**base, "survey_responses": s}
            if outcome is None:
                p.pop("outcome")
            else:
                p["outcome"] = outcome
            out.append(p)
    # Envelope variations.
    out += [
        "not a mapping",
        {},
        {**base, "patient_id": "bad id!"},
        {**base, "follow_up": "soon"},
        {**base, "channel": "fax"},
        {**base, "channel": "sms", "call_sid": "CA" + "0" * 32},
        {**base, "call_sid": "bad/sid"},
        {**base, "call_sid": ""},
        {**base, "dob_verification_result": "maybe"},
        {**base, "session_uuid": "not-a-uuid!"},
        {**base, "session_uuid": "12345678-aaaa-bbbb-cccc-1234567890ab"},
        {**base, "follow_up_reasons": [{"tier": "urgent_same_day", "text": "x"}]},
        {**base, "follow_up_reasons": [{"tier": "x", "text": "x"}]},
        {**base, "follow_up_reasons": "x"},
        {**base, "recording_uri": "gs://x", "outcome": None, "channel": None},
    ]
    return out


def gen_reports(captured: dict) -> list[dict]:
    from voa_core import reports

    items = [{"payload": p, "pattern": None} for p in _report_payloads()]
    items += captured.get("report", [])
    out = []
    seen = set()
    for it in _rt(items):
        s = json.dumps(it, sort_keys=True)
        if s in seen:
            continue
        seen.add(s)
        kw = {"call_id_pattern": it["pattern"]} if it["pattern"] else {}
        clean, errors = reports.validate_report(copy.deepcopy(it["payload"]), **kw)
        out.append({**it, "clean": _rt(clean), "errors": errors})
    return out


# --------------------------------------------------------------------------- (c) instruction

PATIENTS = [
    {"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": False},
    {"first_name": "Jordan", "last_name": "Rivera", "is_diabetic": True},
    {"first_name": "Casey", "last_name": "Morgan", "is_diabetic": "true"},
    {"first_name": "Casey", "last_name": "Morgan", "is_diabetic": "false"},
    {"first_name": "Avery", "last_name": ""},
    {},
    {
        "first_name": "Riley",
        "last_name": "Chen",
        "patient_name": "Riley Chen",
        "patient_id": "PT-2002",
        "dob": "1955-02-03",
        "operation_name": "Knee arthroscopy",
        "operation_date": "2026-10-01",
        "is_diabetic": True,
        "care_setting": "outpatient",
    },
]
KNOWLEDGE = [None, {"ask_satisfied": True}, {"ask_satisfied": False}, {}]


def gen_instruction(captured: dict) -> list[dict]:
    from voa_agents.voice.instruction import build_instruction
    from voa_core.campaign import load_campaign

    items = [
        {"campaign": "post_op", "org_name": org, "patient": p, "knowledge": k}
        for org in ("Example Clinic", "Acme Health")
        for p in PATIENTS
        for k in KNOWLEDGE
    ]
    items += captured.get("instruction", [])
    out, seen = [], set()
    for it in _rt(items):
        s = json.dumps(it, sort_keys=True)
        if s in seen:
            continue
        seen.add(s)
        text = build_instruction(
            load_campaign(it["campaign"]),
            org_name=it["org_name"],
            patient=it["patient"],
            knowledge=it["knowledge"],
        )
        out.append({**it, "text": text})
    return out


# --------------------------------------------------------------------------- (d) tools


def gen_tool_declarations() -> list:
    from voa_agents.voice import tools

    return _rt(
        [
            t._get_declaration().model_dump(mode="json", exclude_none=True)
            for t in tools.build_tools()
        ]
    )


# --------------------------------------------------------------------------- (e) SMS texts

SITE = {
    "org_name": "Acme Health",
    "support_email": "support@example.org",
    "voice_number": "+12025550142",
    "survey_enabled": True,
}


def gen_sms_texts() -> dict:
    from voa_core import copy as messages
    from voa_core import survey
    from voa_core.copy import SiteCopy

    out: dict[str, str] = {}
    for diabetic in (False, True, "true", "false", None):
        ctx = {"is_diabetic": diabetic} if diabetic is not None else {}
        plan = survey.build_plan(ctx)
        out[f"plan[{diabetic!r}]"] = ",".join(plan)
        out[f"preamble[{diabetic!r}]"] = messages.survey_preamble(len(plan))
        for i, f in enumerate(plan):
            out[f"numbered[{diabetic!r}][{f}]"] = messages.question(
                i + 1, len(plan), messages.question_text(f)
            )
    for f, t in sorted(messages.QUESTION_TEXT.items()):
        out[f"question_text[{f}]"] = t
    for f, t in sorted(messages.FOLLOWUP_TEXT.items()):
        out[f"followup_text[{f}]"] = t
        out[f"unclear[{f}]"] = messages.unclear_answer(t)
    for site in ("incision", "knee", "lower back", "chest"):
        out[f"pain_elsewhere[{site}]"] = messages.pain_elsewhere(site)
    out["swelling_trend_only"] = messages.swelling_trend_only()
    out["swelling_side_only"] = messages.swelling_side_only()
    out["another_question"] = messages.another_question()
    for q in ("can I shower", "x" * 80, "  how   much   tylenol  ", ""):
        out[f"question_noted[{q}]"] = messages.question_noted(q)
        out[f"question_noted_last[{q}]"] = messages.question_noted_last(q)
    for asked in (False, True):
        out[f"done_routine[{asked}]"] = messages.survey_done_routine(asked_questions=asked)
        out[f"done_urgent[{asked}]"] = messages.survey_done_urgent(asked_questions=asked)
    out["done_routine[default]"] = messages.survey_done_routine()
    out["done_urgent[default]"] = messages.survey_done_urgent()
    out["cut_short"] = messages.survey_cut_short()
    out["file_failed"] = messages.survey_file_failed()
    out["MEDICATION_LIST_REMINDER"] = messages.MEDICATION_LIST_REMINDER
    out["QUESTIONS_FOLLOW_UP"] = messages.QUESTIONS_FOLLOW_UP
    out["ANOTHER_QUESTION"] = messages.ANOTHER_QUESTION
    out["SWELLING_TREND_ONLY"] = messages.SWELLING_TREND_ONLY
    out["SWELLING_SIDE_ONLY"] = messages.SWELLING_SIDE_ONLY
    # Code-only copy (not campaign data), for completeness.
    for vn in ("+12025550142", ""):
        for enabled in (True, False):
            site = SiteCopy(**{**SITE, "voice_number": vn, "survey_enabled": enabled})
            key = f"[{vn}][{enabled}]"
            out["invite" + key] = messages.invite("Jordan Avery", site)
            out["unclear_choice" + key] = messages.unclear_choice(site)
            out["calling_now" + key] = messages.calling_now(site)
            out["booked" + key] = messages.booked("tomorrow at 3:00 PM", False, site)
            out["booked_adj" + key] = messages.booked("tomorrow at 3:00 PM", True, site)
            out["help" + key] = messages.help_message(site)
            out["opted_out" + key] = messages.opted_out(site)
            out["session_closed" + key] = messages.session_closed(site)
            out["booking_canceled" + key] = messages.booking_canceled(site)
    for name in ("Jordan Avery", "", None):
        out[f"ask_dob[{name}]"] = messages.ask_dob(name)
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
    ):
        out[fn] = getattr(messages, fn)()
    return {"texts": out}


# --------------------------------------------------------------------------- (e) conversations

NOW = "2026-08-17T10:00:00+00:00"
DOB = "1960-06-15"
GOOD_DOB = "06/15/1960"

SCRIPTS: dict[str, tuple[bool, list[str]]] = {
    "all_clear": (False, ["3", GOOD_DOB, "fine", "no", "no", "no", "no", "yes", "no"]),
    "all_clear_diabetic": (
        True,
        ["3", GOOD_DOB, "good", "no", "no", "no", "no", "yes", "in range", "no"],
    ),
    "pain_site_known": (
        False,
        [
            "3",
            GOOD_DOB,
            "my incision is sore",
            "yes",
            "4",
            "no",
            "same",
            "no",
            "no",
            "no",
            "yes",
            "no",
        ],
    ),
    "pain_site_from_reply": (
        False,
        [
            "3",
            GOOD_DOB,
            "ok",
            "yes my knee hurts",
            "8/10",
            "also my hip, aching",
            "worse",
            "no",
            "no",
            "no",
            "no",
            "no",
        ],
    ),
    "pain_unclear_strict": (
        False,
        ["3", GOOD_DOB, "ok", "yes", "a lot", "really a lot", "very much", "lots"],
    ),
    "pain_unclear_then_number": (
        False,
        [
            "3",
            GOOD_DOB,
            "ok",
            "maybe",
            "yes",
            "dunno",
            "seven",
            "my back",
            "b",
            "unsure",
            "no",
            "no",
            "no",
            "no",
            "no",
        ],
    ),
    "swelling_side_known": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "yes, my left leg", "yes", "yes", "no"],
    ),
    "swelling_trend_known": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "yes and it's getting worse", "both", "yes", "no"],
    ),
    "swelling_both_known": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "yes both legs, not changing", "yes", "no"],
    ),
    "swelling_neither": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "yes", "one leg, getting bigger", "no", "no"],
    ),
    "swelling_side_known_reply_no": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "yes my right ankle", "no", "yes", "no"],
    ),
    "incision": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "yes", "red and warm", "no", "yes", "no"],
    ),
    "fever_unknown": (
        False,
        [
            "3",
            GOOD_DOB,
            "ok",
            "no",
            "hmm",
            "not sure really",
            "no",
            "no",
            "maybe",
            "who knows",
            "no",
        ],
    ),
    "sugar_variants": (
        True,
        [
            "3",
            GOOD_DOB,
            "ok",
            "no",
            "no",
            "no",
            "no",
            "yes",
            "65 this morning, 250 at night",
            "a bit high for me",
            "no",
        ],
    ),
    "sugar_number": (True, ["3", GOOD_DOB, "ok", "no", "no", "no", "no", "yes", "250", "no"]),
    "sugar_unclear_twice": (
        True,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "no", "yes", "dunno", "no idea", "no"],
    ),
    "questions_loop": (
        False,
        [
            "3",
            GOOD_DOB,
            "ok",
            "no",
            "no",
            "no",
            "no",
            "yes",
            "yes",
            "how much tylenol can I take",
            "when can I drive?",
            "no",
        ],
    ),
    "questions_inline": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "no", "yes", "can I shower", "thanks"],
    ),
    "questions_cap": (
        False,
        [
            "3",
            GOOD_DOB,
            "ok",
            "no",
            "no",
            "no",
            "no",
            "yes",
            "q one is this?",
            "q two is this?",
            "q three is this?",
            "q four is this?",
            "q five is this?",
            "q six?",
        ],
    ),
    "questions_no_but": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "no", "yes", "no but", "my meds"],
    ),
    "questions_unclear_twice": (
        False,
        ["3", GOOD_DOB, "ok", "no", "no", "no", "no", "yes", "hm", "ok", "driving", "nothing else"],
    ),
    "worried_text": (
        False,
        [
            "3",
            GOOD_DOB,
            "I'm scared, please have a nurse call me",
            "no",
            "no",
            "no",
            "no",
            "no",
            "no",
        ],
    ),
    "cancel_mid": (False, ["3", GOOD_DOB, "ok", "yes", "9", "C"]),
    "cancel_mid_nothing": (False, ["3", GOOD_DOB, "ok", "no", "C"]),
    "red_flag_mid": (False, ["3", GOOD_DOB, "ok", "I have chest pain"]),
    "dob_fail": (False, ["3", "01/01/1990", "02/02/1991"]),
    "dob_unreadable": (False, ["3", "hello", "what", "huh"]),
    "dob_call_now": (False, ["3", "call me"]),
    "after_complete": (
        False,
        ["3", GOOD_DOB, "fine", "no", "no", "no", "no", "yes", "no", "thanks"],
    ),
    "menu_call_now": (False, ["1"]),
    "menu_pick_time": (False, ["2", "tomorrow 3pm", "C"]),
    "menu_unclear": (False, ["blah", "blah"]),
    "stop": (False, ["STOP"]),
    "help": (False, ["HELP"]),
}


def _session(diabetic) -> dict:
    return {
        "state": "awaiting_choice",
        "patient_context": {
            "patient_name": "Jordan Avery",
            "patient_id": "PT-1",
            "patient_dob": DOB,
            "is_diabetic": diabetic,
        },
        "patient_name": "Jordan Avery",
        "call_sid": "CA" + "1" * 32,
        "origin_entry_id": "e1",
        "created_at": "2026-08-17T09:00:00+00:00",
        "retry_count": 0,
    }


def run_conversation(diabetic, replies, *, policy_kw=None) -> dict:
    """Replay a scripted conversation; returns every decision, the outbound texts,
    the closing the API would send, and the report the API would file."""
    from voa_api import sms_service
    from voa_core import copy as messages
    from voa_core import reports
    from voa_core.copy import SiteCopy
    from voa_core.sms import conversation
    from voa_core.sms.conversation import ConversationPolicy, Effect

    site = SiteCopy(**{**SITE, "survey_enabled": False})
    policy = ConversationPolicy(**(policy_kw or {"survey_enabled": True}))
    now = datetime.datetime.fromisoformat(NOW)
    s = _session(diabetic)
    steps, outbound, filings = [], [], []
    for body in replies:
        d = conversation.decide(copy.deepcopy(s), body, now, site=site, policy=policy)
        steps.append({"body": body, "decision": _rt(decision_out(d))})
        outbound += [t for t in (d.reply, *d.extra_replies) if t]
        if d.effect in (Effect.SUBMIT_SURVEY, Effect.FILE_EMERGENCY):
            snap = sms_service.survey_snapshot("+12025550143", s, d)
            if snap is not None:
                payload = sms_service._report_payload(snap)
                clean, errors = reports.validate_report(
                    copy.deepcopy(payload), call_id_pattern=sms_service.CALL_ID_RE
                )
                closing = None
                if not errors and snap.get("complete") and not snap.get("emergency"):
                    asked = conversation.asked_questions(snap.get("answers") or {})
                    fu = payload["follow_up"]
                    closing = (
                        messages.survey_done_urgent(asked_questions=asked)
                        if fu in ("urgent_same_day", "emergency")
                        else messages.survey_done_routine(asked_questions=asked)
                    )
                    outbound.append(closing)
                filings.append(
                    _rt({"snap": snap, "payload": payload, "errors": errors, "closing": closing})
                )
        if d.session_patch:
            s.update(copy.deepcopy(d.session_patch))
        if d.retry is not None:
            s["retry_count"] = d.retry
        if d.state:
            s["state"] = d.state
    return {"steps": steps, "outbound": outbound, "filings": filings, "final_session": _rt(s)}


def gen_conversations(captured: dict) -> dict:
    from voa_core.copy import SiteCopy
    from voa_core.sms import conversation
    from voa_core.sms.conversation import ConversationPolicy
    from voa_core.timeparse import SchedulingPolicy

    scripted = {}
    for name, (diabetic, replies) in SCRIPTS.items():
        scripted[name] = {
            "diabetic": diabetic,
            "replies": replies,
            **run_conversation(diabetic, replies),
        }
    replayed = []
    for it in _rt(captured.get("decide", [])):
        kw = {}
        if it["site"]:
            kw["site"] = SiteCopy(**it["site"])
        if it["policy"]:
            kw["policy"] = ConversationPolicy(**it["policy"])
        if it["scheduling"]:
            kw["scheduling"] = SchedulingPolicy(**it["scheduling"])
        if "site" not in kw:
            continue
        now = datetime.datetime.fromisoformat(it["now"])
        try:
            d = conversation.decide(copy.deepcopy(it["session"]), it["body"], now, **kw)
            out = _rt(decision_out(d))
        except Exception as e:  # recorded as-is: an error is behaviour too
            out = {"error": type(e).__name__}
        replayed.append({**it, "decision": out})
    return {"scripted": scripted, "replayed": replayed}


# --------------------------------------------------------------------------- (f) campaign sequences

PROGRESSIONS = [
    [
        ("overall_feeling", "ok"),
        ("pain_present", "yes"),
        ("pain_severity", "4"),
        ("pain_location", "knee"),
        ("pain_trend", "same"),
        ("fever_reported", "no"),
        ("incision_concerns", "Yes"),
        ("incision_description", "red"),
        ("legs_swollen", True),
        ("swelling_description", "left"),
        ("sleeping_well", "no"),
        ("blood_sugar_status", "high"),
        ("has_questions", "no"),
    ],
    [
        ("overall_feeling", "fine"),
        ("pain_present", "no"),
        ("fever_reported", "unknown"),
        ("incision_concerns", "false"),
        ("legs_swollen", "nope"),
        ("sleeping_well", "yeah"),
        ("has_questions", "yes"),
        ("blood_sugar_status", ""),
        ("blood_sugar_status", "in_range"),
    ],
    [
        ("pain_present", "yeah"),
        ("pain_present", "true"),
        ("pain_present", "Y"),
        ("pain_present", "unknown"),
        ("incision_concerns", "y"),
        ("legs_swollen", "  "),
        ("legs_swollen", "YES"),
    ],
]


def gen_sequences() -> list[dict]:
    from voa_core import survey
    from voa_core.campaign import load_campaign

    c = load_campaign("post_op")
    patients = [
        {"is_diabetic": False},
        {"is_diabetic": True},
        {"is_diabetic": "true"},
        {"is_diabetic": "1"},
        {"is_diabetic": "yes"},
        {},
    ]
    out = []
    for p in patients:
        for prog in PROGRESSIONS:
            answers: dict = {}
            seq = [
                {
                    "applicable": [q.id for q in c.applicable(p, answers)],
                    "missing": c.missing(p, answers),
                    "for_patient": [q.id for q in c.for_patient(p)],
                }
            ]
            for k, v in prog:
                answers[k] = v
                seq.append(
                    {
                        "set": [k, v],
                        "applicable": [q.id for q in c.applicable(p, answers)],
                        "missing": c.missing(p, answers),
                    }
                )
            out.append(
                {
                    "patient": p,
                    "progression": prog,
                    "sequence": seq,
                    "build_plan": survey.build_plan(p),
                }
            )
    for field in [q.id for q in c.questions]:
        for ans in ("yes", "no", "Yes", "unknown", "true", ""):
            out.append(
                {"followups_for": [field, ans], "out": list(survey.followups_for(field, ans))}
            )
    return _rt(out)


# --------------------------------------------------------------------------- (g) voice tools

VOICE_SESSIONS: dict[str, list] = {
    "full_mild": [
        ("record", "overall_feeling", "ok I guess"),
        ("record", "pain_present", "yeah it hurts"),
        ("submit", "questionnaire_completed"),
        ("record", "pain_severity", "hmm"),
        ("record", "pain_severity", "about a nine"),
        ("record", "pain_location", "my knee"),
        ("record", "pain_trend", "about the same"),
        ("record", "fever_reported", "no"),
        ("record", "incision_concerns", "no it looks fine"),
        ("record", "legs_swollen", "no"),
        ("record", "sleeping_well", "pretty well"),
        ("record", "has_questions", "no, that's all"),
        ("submit", "questionnaire_completed"),
        ("end",),
    ],
    "all_clear": [
        ("end",),
        ("record", "overall_feeling", "  great  "),
        ("record", "pain_present", "no"),
        ("record", "fever_reported", "no"),
        ("record", "incision_concerns", "no"),
        ("record", "legs_swollen", "no"),
        ("record", "sleeping_well", "yes"),
        ("record", "blood_sugar_status", "between 120 and 250"),
        ("record", "has_questions", "no"),
        ("submit", "questionnaire_completed"),
        ("end",),
    ],
    "unclear_answers": [
        ("record", "bogus_id", "yes"),
        ("record", "pain_present", "maybe"),
        ("record", "fever_reported", "dunno"),
        ("record", "legs_swollen", "who knows"),
        ("record", "sleeping_well", "eh"),
        ("record", "incision_concerns", "hmm"),
        ("record", "has_questions", "uh"),
        ("record", "pain_trend", "eh"),
        ("record", "blood_sugar_status", "I don't check it"),
        ("record", "overall_feeling", ""),
        ("record", "pain_location", ""),
        ("record", "swelling_description", "   "),
        ("submit", "questionnaire_completed"),
    ],
    "questions": [
        ("record", "overall_feeling", "fine"),
        ("record", "pain_present", "no"),
        ("record", "fever_reported", "no"),
        ("record", "incision_concerns", "yes"),
        ("submit", "questionnaire_completed"),
        ("record", "incision_description", "it is red and warm"),
        ("record", "legs_swollen", "yes"),
        ("record", "swelling_description", "just my left leg"),
        ("record", "sleeping_well", "no"),
        ("question", "how much tylenol can I take"),
        ("question", ""),
        ("question", "when can I drive"),
        ("question", "when can I drive"),
        ("question", "is my wound ok"),
        ("question", "can I shower"),
        ("question", "one more"),
        ("submit", "questionnaire_completed"),
    ],
    "has_questions_yes_none": [
        ("record", "overall_feeling", "fine"),
        ("record", "pain_present", "no"),
        ("record", "fever_reported", "yes"),
        ("record", "incision_concerns", "no"),
        ("record", "legs_swollen", "no"),
        ("record", "sleeping_well", "yes"),
        ("record", "has_questions", "yes"),
        ("submit", "questionnaire_completed"),
    ],
    "exits": [
        ("submit", "nonsense"),
        ("submit", "reschedule_requested"),
        ("submit", "unable_to_reach_wrong_person"),
        ("submit", "consent_declined"),
        ("submit", "voicemail_left"),
        ("submit", "identity_verification_failed"),
        ("submit", "emergency_ended"),
        ("end",),
    ],
}


async def _voice_session(steps, state: dict) -> dict:
    from voa_agents.voice import tools

    c = SimpleNamespace(state=copy.deepcopy(state))
    results = []
    for step in steps:
        kind = step[0]
        if kind == "record":
            r = await tools.record_answer(step[1], step[2], c)
        elif kind == "question":
            r = await tools.record_patient_question(step[1], c)
        elif kind == "submit":
            r = await tools.submit_report(step[1], c)
        else:
            r = await tools.end_call("done", c)
        results.append(_rt(r))
    final = {k: v for k, v in c.state.items() if k != "tool_audit"}
    audit = [{k: v for k, v in a.items() if k != "t"} for a in c.state.get("tool_audit", [])]
    return {"results": results, "final_state": _rt(final), "audit": _rt(audit)}


def gen_voice() -> list[dict]:
    states = {
        "campaign_post_op": {"campaign_id": "post_op", "patient": {"dob": "1950-03-15"}},
        "campaign_post_op_diabetic": {
            "campaign_id": "post_op",
            "patient": {"dob": "1950-03-15", "is_diabetic": True, "patient_id": "PT-3"},
        },
    }
    out = []
    for sname, state in states.items():
        for name, steps in VOICE_SESSIONS.items():
            res = asyncio.run(_voice_session(steps, state))
            out.append({"state": sname, "initial": state, "session": name, "steps": steps, **res})
    from voa_agents.voice import tools
    from voa_core.campaign import load_campaign

    c = load_campaign("post_op")
    for tier in (
        "emergency",
        "urgent_same_day",
        "routine_next_business_day",
        "informational",
        "none",
    ):
        for asked in (False, True):
            for camp in (c, None):
                out.append(
                    {
                        "closing_for": [tier, asked, camp is not None],
                        "out": tools.closing_for(camp, tier, asked),
                    }
                )
    return _rt(out)


# --------------------------------------------------------------------------- main


def main() -> None:
    captured = json.loads(CAPTURED.read_text()) if CAPTURED.exists() else {}
    if not captured:
        print("warning: no captured_inputs.json; only the synthetic corpus", file=sys.stderr)
    cases = gen_triage(captured)
    # Split: the repo's PII scanner reads files up to 2 MB.
    for old in GOLDEN.glob("triage*.json"):
        old.unlink()
    for i in range(0, len(cases), TRIAGE_CHUNK):
        _dump(f"triage_{i // TRIAGE_CHUNK}.json", cases[i : i + TRIAGE_CHUNK])
    _dump("reports.json", gen_reports(captured))
    _dump("instruction.json", gen_instruction(captured))
    _dump("tool_declarations.json", gen_tool_declarations())
    _dump("sms_texts.json", gen_sms_texts())
    conv = gen_conversations(captured)
    for part in ("scripted", "replayed"):  # two files: each under the 1 MB limit
        _dump(f"sms_conversations_{part}.json", {part: conv[part]})
    _dump("campaign_sequences.json", gen_sequences())
    _dump("voice_tools.json", gen_voice())


if __name__ == "__main__":
    main()
