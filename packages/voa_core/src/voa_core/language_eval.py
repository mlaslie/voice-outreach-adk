"""The non-English safety evaluation (docs/LANGUAGES-PLAN.md §6, step 3).

Runs ``config/languages/eval/safety.yaml`` through the same functions the live
paths use, with any :class:`~voa_core.translate.Translator`:

* offline CI: a :class:`~voa_core.translate.FakeTranslator` built from the
  corpus's own English (``fake_translator``), so the rules are what is tested;
* ``make eval-languages``: the real Gemini translator, so the translations are.

Gates (``gate_failures``): red-flag recall 1.0; no "don't know" read as yes or
no; every mixed question immediate; every STOP word opts out.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import checks, dob, intent, parsers, translate
from . import patient_questions as pq

CORPUS = Path("languages") / "eval" / "safety.yaml"
#: Birthdates in the corpus are checked against this "today" (two-digit years).
TODAY = datetime.date(2026, 10, 22)


def corpus_path(config_dir: Path | None = None) -> Path:
    from .campaign import default_config_dir

    return (config_dir or default_config_dir()) / CORPUS


def load(config_dir: Path | None = None) -> list[dict[str, Any]]:
    doc = yaml.safe_load(corpus_path(config_dir).read_text()) or {}
    return [dict(r) for r in doc.get("rows") or []]


def fake_translator(rows: Sequence[Mapping[str, Any]]) -> translate.FakeTranslator:
    """Maps each input to its corpus English (an input that IS its English, like
    a numeric date, reads as English)."""
    table = {
        str(r["input"]): (str(r["language"]).split("-")[0], str(r["english"]))
        for r in rows
        if str(r["english"]) != str(r["input"])
    }
    return translate.FakeTranslator(table)


@dataclass
class RowResult:
    id: str
    kind: str
    language: str
    channel: str
    input: str
    english: str  # what the translator produced (or the input when English)
    expected: Any
    got: Any
    ok: bool
    misread: bool = False  # "don't know" read as yes/no, or a yes read as no


@dataclass
class Report:
    rows: list[RowResult] = field(default_factory=list)

    def of(self, kind: str, expect: Any = None) -> list[RowResult]:
        return [r for r in self.rows if r.kind == kind and (expect is None or r.expected == expect)]

    @staticmethod
    def _rate(rows: list[RowResult]) -> float | None:
        return round(sum(r.ok for r in rows) / len(rows), 4) if rows else None

    def metrics(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows),
            "red_flag_recall": self._rate(self.of("red_flag", True)),
            "negated_red_flags_not_flagged": self._rate(self.of("red_flag", False)),
            "dont_know_read_as_yes_or_no": sum(r.misread for r in self.of("unknown")),
            "yes_no_correct_or_reasked": self._rate(self.of("yes_no")),
            "yes_no_misread": sum(r.misread for r in self.of("yes_no")),
            "pain_in_words": self._rate(self.of("pain")),
            "dob": self._rate(self.of("dob")),
            "mixed_question_immediate": self._rate(self.of("question")),
            "swelling_urgent": self._rate(self.of("swelling")),
            "stop_opts_out": self._rate(self.of("stop")),
            "help": self._rate(self.of("help")),
        }

    def gate_failures(self) -> list[str]:
        m = self.metrics()
        out = []
        # A rate is None when the corpus has no rows of that kind: nothing to gate.
        if m["red_flag_recall"] not in (1.0, None):
            out.append(f"red-flag recall {m['red_flag_recall']} (must be 1.0)")
        if m["dont_know_read_as_yes_or_no"]:
            out.append(f"{m['dont_know_read_as_yes_or_no']} 'don't know' read as yes or no")
        if m["mixed_question_immediate"] not in (1.0, None):
            out.append(f"mixed questions immediate {m['mixed_question_immediate']} (must be 1.0)")
        if m["stop_opts_out"] not in (1.0, None):
            out.append(f"STOP words {m['stop_opts_out']} (must be 1.0)")
        return out

    def failures(self) -> list[RowResult]:
        return [r for r in self.rows if not r.ok]


def _non_english(language: str) -> bool:
    return not translate.is_english(language)


def check_row(row: Mapping[str, Any], english: str) -> RowResult:
    """One row through the live code paths, given the translator's English."""
    kind, channel = str(row["kind"]), str(row.get("channel") or "sms")
    original, expect = str(row["input"]), row.get("expect")
    lang = str(row.get("language") or "")
    misread = False
    got: Any
    if kind == "red_flag":
        # SMS: the original with every list, then the translation (read_inbound
        # + decide). Voice and transcripts: the post-call re-check on the English.
        got = intent.is_red_flag(original) or intent.is_red_flag(english, translated=True)
        ok = got is bool(expect)
    elif kind in ("unknown", "yes_no"):
        if channel == "voice-tool" and parsers.not_english(original):
            got = "refused"  # record_answer: "record the answer in English"
        else:
            got = parsers.parse_yes_no(english if channel != "voice-tool" else original)
        if kind == "unknown":
            misread = got in ("yes", "no")
            ok = not misread
        else:
            misread = got in ("yes", "no") and got != expect
            ok = got in (expect, "refused")
    elif kind == "pain":
        got = parsers.parse_scale(english)
        ok = got == expect
    elif kind == "dob":
        day_first = _non_english(lang)
        heard = dob.parse_translated(original, english, TODAY, day_first=day_first)
        got = heard.isoformat() if heard else None
        ok = got == str(expect)
    elif kind == "question":
        # In the call (the original, fail safe) and after it (the English).
        en = pq.classify(english).priority
        orig = pq.classify(original).priority
        ok = en == pq.PRIORITY_IMMEDIATE and orig == pq.PRIORITY_IMMEDIATE
        got = "immediate" if ok else f"english={en}, original={orig}"
    elif kind == "swelling":
        ok = checks.swelling_is_urgent(english) and checks.swelling_is_urgent(original)
        got = "urgent" if ok else "routine"
    elif kind in ("stop", "help"):
        c = intent.classify_compliance(original)
        got = c.value if c else None
        ok = got == expect
    else:
        raise ValueError(f"unknown kind {kind!r} in row {row.get('id')}")
    return RowResult(
        id=str(row.get("id")),
        kind=kind,
        language=lang,
        channel=channel,
        input=original,
        english=english,
        expected=expect,
        got=got,
        ok=bool(ok),
        misread=misread,
    )


async def run(rows: Sequence[Mapping[str, Any]], translator) -> Report:
    """Every row, translated in one batch (the translator chunks it)."""
    inputs = [str(r["input"]) for r in rows]
    results = await translator.translate_turns(inputs, target="en")
    if len(results) != len(rows):
        raise translate.TranslationFailed("wrong number of translations")
    report = Report()
    for row, r in zip(rows, results, strict=True):
        report.rows.append(check_row(row, r.text_en or str(row["input"])))
    return report
