"""The use-case spec validator: :func:`campaign_problems`.

One validator for every place a spec is checked (tests and CI today; the admin
UI, the API and the CLI later -- docs/USE-CASES-PLAN.md section 4). Each problem
is one line a person can act on, in the file's own terms: where (question id or
key), what is wrong, and what to do.

It covers:

* the flow: unique ids, ``when`` pointing at an earlier top-level question (one
  level of follow-ups), ``when_patient`` testing declared patient fields;
* types: a parser for every question, scale and choice settings, named parsers,
  ``reuse`` helpers, ``required_with`` sub-answers;
* rules: tiers, expressions that parse against the spec's ids, named checks and
  their arguments, reason placeholders;
* the voice scripts the frame needs, and their placeholders;
* the SMS copy (when the spec has the sms channel): every line present, the
  placeholders each may use and must keep, plain GSM-7;
* the guardrails: the shared red flags are never removed;
* the spec's own ``tests``, run through the rule engine.
"""

from __future__ import annotations

from .campaign import SHARED_RED_FLAGS, Campaign
from .copy import GSM7, placeholders

#: Placeholders the voice scripts may use (filled in by the instruction builder).
VOICE_PLACEHOLDERS = frozenset({"org_name", "patient_first_name", "patient_last_name"})
#: Voice script keys the frame says. closing_questions only with an open_questions
#: question; closing_extra is optional.
VOICE_SCRIPTS_REQUIRED = (
    "opener",
    "identity_prompt",
    "identity_verified",
    "consent",
    "wrong_person",
    "identity_failed",
    "reschedule",
    "emergency",
    "no_medical_advice",
    "closing_follow_up",
    "closing_no_follow_up",
    "voicemail",
)
VOICE_SCRIPTS_OPTIONAL = ("closing_questions", "closing_extra")

#: The SMS questionnaire's own lines: key -> placeholders it may use (always).
SMS_SCRIPTS_BASE: dict[str, frozenset[str]] = {
    "preamble": frozenset({"total"}),
    "numbered_question": frozenset({"number", "total", "question"}),
    "unclear_answer": frozenset({"question"}),
    "closing_routine": frozenset(),
    "closing_urgent": frozenset(),
    "cut_short": frozenset(),
    "file_failed": frozenset(),
}
#: Lines that exist only with an open_questions question (the questions loop).
SMS_SCRIPTS_QUESTIONS: dict[str, frozenset[str]] = {
    "ask_question": frozenset(),
    "another_question": frozenset(),
    "question_noted": frozenset({"question", "another_question"}),
    "question_noted_last": frozenset({"question"}),
    "questions_follow_up": frozenset(),
}
_CLOSINGS = ("closing_routine", "closing_urgent")
_VARIANT_FIELDS = ("sms_if_known", "sms_if_side_known", "sms_if_trend_known")


def sms_script_placeholders(c: Campaign) -> dict[str, frozenset[str]]:
    """sms_scripts key -> the placeholders it may use, for this spec. All keys are required."""
    out = dict(SMS_SCRIPTS_BASE)
    extra: set[str] = set()
    if c.has_open_questions:
        out.update(SMS_SCRIPTS_QUESTIONS)
        extra.add("questions_follow_up")
    if c.sms_scripts.get("closing_extra", "").strip():
        out["closing_extra"] = frozenset()
        extra.add("closing_extra")
    for k in _CLOSINGS:
        out[k] = frozenset(extra)
    return out


def sms_script_required(c: Campaign) -> dict[str, frozenset[str]]:
    """Placeholders a template must keep: without them the message loses its point.
    The closings keep the questions follow-up line (with a questions loop) and the
    closing_extra line (when the spec has one: post-op's medication-list reminder,
    a clinic request of 19 Oct 2026)."""
    out = {
        "numbered_question": frozenset({"question"}),
        "unclear_answer": frozenset({"question"}),
    }
    keep = sms_script_placeholders(c)["closing_routine"]
    for k in _CLOSINGS:
        out[k] = keep
    return out


def _flow_problems(c: Campaign) -> list[str]:
    from .parsers import NAMED_PARSERS
    from .survey import FOLLOWUP_REUSE

    out: list[str] = []
    ids = [q.id for q in c.questions]
    patient_ids = {f.id for f in c.patient_fields}
    seen: dict = {}
    if not c.questions:
        out.append("questions: a use case needs at least one question")
    for q in c.questions:
        if q.id in seen:
            out.append(f"{q.id}: listed twice")
        if q.id.startswith("_") or q.id in ("patient_questions", "patient"):
            out.append(f"{q.id}: reserved name; choose another id")
        for key, value in q.when.items():
            if key not in ids:
                out.append(f"{q.id}: `when` refers to unknown question {key!r}")
            elif key not in seen:
                out.append(f"{q.id}: `when` refers to {key!r}, which is not asked before it")
            elif seen[key].when:
                out.append(
                    f"{q.id}: `when` refers to {key!r}, which is itself a follow-up "
                    "(the text flow has one level of follow-ups)"
                )
            if not str(value).strip():
                out.append(f"{q.id}: `when` needs the answer that triggers it")
        if len(q.when) > 1:
            out.append(f"{q.id}: `when` may test only one earlier answer")
        if q.when and q.when_patient:
            out.append(f"{q.id}: a follow-up cannot also have `when_patient`")
        for key in q.when_patient:
            if key not in patient_ids:
                out.append(
                    f"{q.id}: `when_patient` tests unknown flag {key!r} "
                    "(declare it under patient_fields)"
                )
        # Types.
        if q.parser and q.parser not in NAMED_PARSERS:
            out.append(
                f"{q.id}: unknown parser {q.parser!r} (known: {', '.join(sorted(NAMED_PARSERS))}; "
                "a new one is a code change in voa_core/parsers.py)"
            )
        if q.kind == "scale":
            lo, hi = q.scale_range()
            if q.type == "scale" and (q.min is None or q.max is None):
                out.append(f"{q.id}: a scale needs `min` and `max`")
            elif lo >= hi:
                out.append(f"{q.id}: scale `min` must be below `max`")
        if q.kind == "choice" and not q.parser and len(q.choices) < 2:
            out.append(f"{q.id}: a choice question needs at least two `choices`")
        if q.choices and q.kind != "choice":
            out.append(f"{q.id}: `choices` is only for type choice")
        if q.kind == "open_questions" and q.when:
            out.append(f"{q.id}: the questions loop cannot be a follow-up")
        if q.reuse:
            if q.reuse not in FOLLOWUP_REUSE:
                out.append(
                    f"{q.id}: unknown reuse helper {q.reuse!r} "
                    f"(known: {', '.join(sorted(FOLLOWUP_REUSE))})"
                )
            if not q.when:
                out.append(f"{q.id}: `reuse` is only for a follow-up (a question with `when`)")
        if q.required_with is not None:
            rw = q.required_with
            if q.when:
                out.append(f"{q.id}: `required_with` belongs on a top-level question")
            for f in rw.fields:
                fq = c.question(f)
                if fq is None:
                    out.append(f"{q.id}: `required_with` names unknown question {f!r}")
                elif fq.when.get(q.id) is None:
                    out.append(f"{q.id}: `required_with` field {f!r} is not a follow-up of {q.id}")
            try:
                bad = placeholders(rw.message) - {"missing"}
            except ValueError as e:
                out.append(f"{q.id}: `required_with.message` has bad braces ({e})")
            else:
                if bad:
                    out.append(
                        f"{q.id}: `required_with.message` may only use {{missing}}, "
                        f"not {sorted(bad)}"
                    )
        for field in ("ask", "note"):
            try:
                if placeholders(getattr(q, field)):
                    out.append(
                        f"{q.id}.{field}: no {{placeholders}} here (the text reaches the "
                        "voice model as written)"
                    )
            except ValueError as e:
                out.append(f"{q.id}.{field}: bad braces ({e})")
        seen[q.id] = q
    if len(c.open_question_ids) > 1:
        out.append("questions: at most one open_questions question")
    pf = [f.id for f in c.patient_fields]
    for f in sorted({f for f in pf if pf.count(f) > 1}):
        out.append(f"patient_fields.{f}: listed twice")
    return out


def _rule_problems(c: Campaign) -> list[str]:
    from .rules import RULE_TIERS, RuleSyntaxError, check_call_problem, compile_rule_when

    out: list[str] = []
    ids = frozenset(c.question_ids)
    for i, r in enumerate(c.rules, 1):
        where = f"rules[{i}]" + (f" ({r.name})" if r.name else "")
        if r.tier not in RULE_TIERS:
            out.append(f"{where}: tier {r.tier!r} must be one of {', '.join(RULE_TIERS)}")
        if not (r.when or r.check):
            out.append(f"{where}: needs `when`, `check` or both")
        if r.when:
            try:
                compile_rule_when(c, r.when, under=bool(r.any_of))
            except RuleSyntaxError as e:
                out.append(f"{where}: `when` {e}")
        if r.any_of:
            if r.check:
                out.append(f"{where}: `any_of` cannot be combined with `check`")
            for qid in r.any_of:
                if qid not in ids:
                    out.append(f"{where}: `any_of` names unknown question {qid!r}")
        if r.check:
            if p := check_call_problem(r.check, tuple(r.args), ids):
                out.append(f"{where}: {p}")
        elif r.args:
            out.append(f"{where}: `args` without a `check`")
        for f in r.fields:
            if f not in ids and f != "patient_questions":
                out.append(f"{where}: `fields` names unknown question {f!r}")
        try:
            for name in placeholders(r.reason):
                if name not in ids:
                    out.append(f"{where}: reason placeholder {{{name}}} is not a question id")
        except ValueError as e:
            out.append(f"{where}: reason has bad braces ({e})")
        if not r.reason.strip() and r.check not in ("questions_for_care_team",):
            out.append(f"{where}: needs a `reason` (what the nurse reads)")
    return out


def _check_text(
    out: list[str], where: str, text: str, allowed: frozenset[str], required=frozenset()
) -> None:
    try:
        names = placeholders(text)
    except ValueError as e:
        out.append(f"{where}: bad braces ({e}); use {{name}} placeholders only")
        return
    for n in sorted(names - allowed):
        ok = ", ".join(f"{{{a}}}" for a in sorted(allowed)) or "none"
        out.append(f"{where}: unknown placeholder {{{n}}} (allowed: {ok})")
    for n in sorted(required - names):
        out.append(f"{where}: must keep the {{{n}}} placeholder")


def _voice_problems(c: Campaign) -> list[str]:
    out: list[str] = []
    if "voice" not in c.channels:
        return out
    required = list(VOICE_SCRIPTS_REQUIRED)
    if c.has_open_questions:
        required.append("closing_questions")
    for key in required:
        if not c.scripts.get(key, "").strip():
            out.append(f"scripts.{key}: missing (the voice call says it)")
    known = set(VOICE_SCRIPTS_REQUIRED) | set(VOICE_SCRIPTS_OPTIONAL)
    for key in sorted(set(c.scripts) - known):
        out.append(f"scripts.{key}: unknown line (nothing says it)")
    for key, text in c.scripts.items():
        _check_text(out, f"scripts.{key}", text, VOICE_PLACEHOLDERS)
    if c.closing_extra_label and not c.closing_extra.strip():
        out.append("closing_extra_label: set, but scripts.closing_extra is empty")
    return out


def sms_copy_problems(c: Campaign) -> list[str]:
    """Why this spec's SMS wording cannot be sent (empty if it can)."""
    from .survey import FOLLOWUP_REUSE

    out: list[str] = []

    def check(where: str, text: str, allowed: frozenset[str], required=frozenset()) -> None:
        _check_text(out, where, text, allowed, required)
        bad = sorted(set(text) - GSM7)
        if bad:
            out.append(f"{where}: not plain GSM-7 text: {''.join(bad)!r}")

    for q in c.questions:
        if not q.sms.strip():
            out.append(f"{q.id}: missing `sms` (every question is asked by text too)")
        else:
            check(f"{q.id}.sms", q.sms, frozenset())
        variants = FOLLOWUP_REUSE.get(q.reuse, {})
        for field in _VARIANT_FIELDS:
            text = getattr(q, field)
            if field in variants:
                if not text.strip():
                    out.append(f"{q.id}: missing `{field}`")
                else:
                    check(f"{q.id}.{field}", text, variants[field])
            elif text:
                out.append(f"{q.id}: `{field}` is not used for this question")
    allowed = sms_script_placeholders(c)
    required = sms_script_required(c)
    for key in sorted(set(c.sms_scripts) - set(allowed)):
        if key == "closing_extra":
            continue  # empty: allowed and unused
        out.append(f"sms_scripts.{key}: unknown line (nothing sends it)")
    for key, may in allowed.items():
        text = c.sms_scripts.get(key, "")
        if not text.strip():
            out.append(f"sms_scripts.{key}: missing")
        else:
            check(f"sms_scripts.{key}", text, may, required.get(key, frozenset()))
    return out


def _guardrail_problems(c: Campaign) -> list[str]:
    out: list[str] = []
    if c.red_flags is not None:
        for flag in SHARED_RED_FLAGS:
            if flag not in c.red_flags:
                out.append(
                    f"red_flags: the shared red flag {flag!r} is missing (a spec may add "
                    "red flags with red_flags_extra, never remove one)"
                )
    for f in c.red_flags_extra:
        if not f.strip():
            out.append("red_flags_extra: an empty entry")
    return out


def _schema_only_problems(c: Campaign) -> list[str]:
    out: list[str] = []
    ids = set(c.question_ids)
    pids = {f.id for f in c.patient_fields}
    for f in c.display.facts:
        if f not in pids:
            out.append(f"display.facts: {f!r} is not a patient field")
    for item in c.display.findings:
        refs = [item.from_] if isinstance(item.from_, str) else item.from_
        for r in refs:
            if r not in ids:
                out.append(f"display.findings ({item.label}): {r!r} is not a question id")
    for m in c.analytics.measures:
        if m not in ids:
            out.append(f"analytics.measures: {m!r} is not a question id")
        elif (q := c.question(m)) is not None and q.kind == "text" and not q.parser:
            out.append(f"analytics.measures: {m!r} is free text, which is never exported")
    for f in c.knowledge.context:
        if f not in pids:
            out.append(f"knowledge.context: {f!r} is not a patient field")
    return out


def campaign_problems(c: Campaign, *, run_tests: bool = True) -> list[str]:
    """Everything wrong with a spec, one human-readable line each (empty: none)."""
    from .rules import spec_test_failures

    out = _flow_problems(c) + _rule_problems(c) + _voice_problems(c)
    if "sms" in c.channels:
        out += sms_copy_problems(c)
    out += _guardrail_problems(c) + _schema_only_problems(c)
    if not c.channels:
        out.append("channels: at least one of voice, sms")
    if run_tests and not any(p.startswith("rules[") for p in out):
        out += spec_test_failures(c)
    return out
