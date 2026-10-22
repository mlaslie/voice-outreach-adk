"""The non-English safety corpus, offline (docs/LANGUAGES-PLAN.md §6, step 3).

``config/languages/eval/safety.yaml`` through the live safety code with a
FakeTranslator that returns the corpus's own English, so this tests the rules,
not the model. ``make eval-languages`` runs the same corpus through Gemini."""

from __future__ import annotations

import asyncio

from voa_core import language_eval


def _report():
    rows = language_eval.load()
    return rows, asyncio.run(language_eval.run(rows, language_eval.fake_translator(rows)))


def test_the_corpus_covers_what_the_plan_asks_for():
    rows, _ = _report()
    by = {}
    for r in rows:
        by.setdefault(r["kind"], set()).add(r["language"].split("-")[0])
    for lang in ("es", "vi", "ru", "ar", "hi"):
        assert lang in by["red_flag"] and lang in by["unknown"] and lang in by["yes_no"]
    assert {"pain", "dob", "question", "swelling", "stop", "help"} <= set(by)
    negated = [r for r in rows if r["kind"] == "red_flag" and r["expect"] is False]
    assert {r["language"].split("-")[0] for r in negated} >= {"es", "vi", "ru", "ar", "hi"}
    assert {r["channel"] for r in rows} == {"voice-tool", "sms", "transcript"}
    assert any(r["id"] == "q-es-1" and "dieta" in r["input"] for r in rows)


def test_the_gates_hold():
    _, report = _report()
    assert report.gate_failures() == []
    m = report.metrics()
    assert m["red_flag_recall"] == 1.0
    assert m["dont_know_read_as_yes_or_no"] == 0
    assert m["mixed_question_immediate"] == 1.0
    assert m["stop_opts_out"] == 1.0


def test_every_row_passes_with_the_corpus_english():
    _, report = _report()
    assert [(r.id, r.got) for r in report.failures()] == []


def test_a_gate_fails_when_a_red_flag_is_missed():
    rows = [
        {
            "id": "x",
            "language": "es-US",
            "channel": "sms",
            "kind": "red_flag",
            "expect": True,
            "input": "algo raro",
            "english": "something odd",
        }
    ]
    report = asyncio.run(language_eval.run(rows, language_eval.fake_translator(rows)))
    assert report.gate_failures() == ["red-flag recall 0.0 (must be 1.0)"]
