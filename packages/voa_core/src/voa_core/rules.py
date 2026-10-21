"""The triage rule engine: a spec's ``rules`` evaluated over the answers.

A rule says *when* (an expression), optionally a named *check* (reviewed Python,
``voa_core.checks``), and the *tier* and *reason* of the finding it adds. Every
matching rule is a finding; the findings are ordered most urgent first (rule
order within a tier) and the most urgent tier is the call's follow-up.

The expression language is deliberately tiny and is parsed here by hand: no
``eval``, no ``ast``, nothing that can run code. Grammar::

    expr     := or
    or       := and ("or" and)*
    and      := not ("and" not)*
    not      := "not" not | compare
    compare  := term [ OP term | ["not"] "in" list ]      OP: == != < <= > >=
    term     := NUMBER | STRING | NAME | call | "(" expr ")"
    call     := answered(ID) | mentions(ID, list) | check(CHECK [, ARG]*)
    list     := "[" [literal ("," literal)*] "]"
    literal  := NUMBER | STRING | NAME | true | false | none
    NAME     := a question id, "patient.<field>", "_" (in an any_of rule), or a
                bare word, which is a string literal ("yes", "worse")

Semantics (chosen to match the pre-engine post-op code exactly):

* An answer reference is the recorded value. ``scale`` answers count only when
  they are whole numbers (anything else reads as no answer); ``number`` answers
  only when numeric. ``patient.<field>`` reads the patient context (bool fields
  are coerced: "true"/"1" are true).
* ``==`` / ``!=`` / ``in`` compare exactly ("Yes" is not "yes").
* ``<``, ``<=``, ``>``, ``>=`` are true only when both sides are numbers.
* ``answered(x)``: a number for numeric types; non-blank text otherwise.
* ``mentions(x, [words])``: x's text contains one of the words or phrases, whole
  word, case-insensitive, not negated ("no fever" does not mention fever).
* ``check(name, args...)``: a named check is truthy.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from .checks import NAMED_CHECKS, CheckContext, Hit, run_check
from .compliance import coerce_bool
from .parsers import _negated_words, _norm

if TYPE_CHECKING:
    from .campaign import Campaign

FOLLOW_UP_EMERGENCY = "emergency"
FOLLOW_UP_URGENT = "urgent_same_day"
FOLLOW_UP_ROUTINE = "routine_next_business_day"
FOLLOW_UP_INFO = "informational"
FOLLOW_UP_NONE = "none"
#: Most to least urgent.
TIER_ORDER: tuple[str, ...] = (
    FOLLOW_UP_EMERGENCY,
    FOLLOW_UP_URGENT,
    FOLLOW_UP_ROUTINE,
    FOLLOW_UP_INFO,
)
#: Tiers a spec rule may produce. Emergency is the frame's (red flags, 911), never a rule's.
RULE_TIERS: tuple[str, ...] = (FOLLOW_UP_URGENT, FOLLOW_UP_ROUTINE, FOLLOW_UP_INFO)

#: How a lower tier reads inside a reason sentence.
TIER_SHORT = {
    FOLLOW_UP_EMERGENCY: "emergency",
    FOLLOW_UP_URGENT: "same day",
    FOLLOW_UP_ROUTINE: "next business day",
    FOLLOW_UP_INFO: "noted",
    FOLLOW_UP_NONE: "none",
}
NO_FINDINGS_REASON = "No concerning symptoms reported."


@dataclass(frozen=True)
class Triage:
    follow_up: str
    reason: str
    # The answer fields that caused the escalation. Computed here rather than
    # re-derived in the dashboard: which answer made a nurse necessary is a
    # clinical judgement, and having two implementations of it -- one that
    # decides and one that colours the screen -- is how they drift apart.
    triggers: tuple[str, ...] = ()
    # Every finding, most urgent first: {tier, text, fields, source}. Stored on the
    # report and call doc as ``follow_up_reasons``.
    findings: tuple[dict, ...] = ()

    @property
    def reasons(self) -> list[dict]:
        return [dict(f) for f in self.findings]


# =============================================================================
# The expression language
# =============================================================================


class RuleSyntaxError(ValueError):
    pass


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>-?\d+(?:\.\d+)?)
      | (?P<str>"[^"]*"|'[^']*')
      | (?P<op>==|!=|<=|>=|<|>)
      | (?P<punct>[()\[\],])
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
    )""",
    re.X,
)
_KEYWORDS = frozenset({"and", "or", "not", "in"})
_FUNCTIONS = frozenset({"answered", "mentions", "check"})
_CONSTANTS = {"true": True, "false": False, "none": None}
MAX_EXPR_CHARS = 500


def _tokenize(text: str) -> list[tuple[str, Any, int]]:
    if len(text) > MAX_EXPR_CHARS:
        raise RuleSyntaxError(f"expression longer than {MAX_EXPR_CHARS} characters")
    out: list[tuple[str, Any, int]] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            bad = text[pos:].strip()[:15]
            raise RuleSyntaxError(f"cannot read {bad!r} (at character {pos + 1})")
        kind = m.lastgroup
        raw = m.group(kind)
        if kind == "num":
            out.append(("lit", float(raw) if "." in raw else int(raw), m.start(kind)))
        elif kind == "str":
            out.append(("lit", raw[1:-1], m.start(kind)))
        elif kind == "name" and raw in _KEYWORDS:
            out.append((raw, raw, m.start(kind)))
        else:
            out.append((kind, raw, m.start(kind)))
        pos = m.end()
    return out


class _Parser:
    """Recursive descent over the tokens. Names are resolved against the spec's ids
    as they are parsed, so a typo is a problem at lint time, not a silent literal."""

    def __init__(self, text: str, ids: frozenset[str], patient_ids: frozenset[str], under: bool):
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0
        self.ids, self.patient_ids, self.under = ids, patient_ids, under

    # -- helpers
    def peek(self, k: int = 0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("end", None, len(self.text))

    def take(self, kind: str, value: Any = None):
        t = self.peek()
        if t[0] != kind or (value is not None and t[1] != value):
            want = value or kind
            got = "the end" if t[0] == "end" else repr(t[1])
            raise RuleSyntaxError(f"expected {want!r} but found {got} (at character {t[2] + 1})")
        self.i += 1
        return t

    # -- grammar
    def parse(self):
        if not self.toks:
            raise RuleSyntaxError("empty expression")
        node = self.or_()
        if self.peek()[0] != "end":
            t = self.peek()
            raise RuleSyntaxError(f"unexpected {t[1]!r} (at character {t[2] + 1})")
        return node

    def or_(self):
        parts = [self.and_()]
        while self.peek()[0] == "or":
            self.i += 1
            parts.append(self.and_())
        return parts[0] if len(parts) == 1 else ("or", parts)

    def and_(self):
        parts = [self.not_()]
        while self.peek()[0] == "and":
            self.i += 1
            parts.append(self.not_())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def not_(self):
        if self.peek()[0] == "not":
            self.i += 1
            return ("not", self.not_())
        return self.compare()

    def compare(self):
        left = self.term()
        t = self.peek()
        if t[0] == "op":
            self.i += 1
            right = self.term()
            node = ("cmp", t[1], left, right)
        elif t[0] == "in" or (t[0] == "not" and self.peek(1)[0] == "in"):
            neg = t[0] == "not"
            self.i += 2 if neg else 1
            node = ("in", left, self.list_(), neg)
            right = None
        else:
            return left
        sides = [left] if right is None else [left, right]
        if not any(s[0] in ("ref", "pref", "under") for s in sides):
            raise RuleSyntaxError(
                f"{self.text!r}: neither side refers to an answer or a patient field "
                "(check the question id's spelling)"
            )
        return node

    def term(self):
        t = self.peek()
        if t[0] == "lit":
            self.i += 1
            return ("lit", t[1])
        if t[0] == "punct" and t[1] == "(":
            self.i += 1
            node = self.or_()
            self.take("punct", ")")
            return node
        if t[0] == "name":
            name = t[1]
            if self.peek(1)[:2] == ("punct", "("):
                return self.call()
            self.i += 1
            return self.name(name, t[2])
        got = "the end" if t[0] == "end" else repr(t[1])
        raise RuleSyntaxError(f"expected a value but found {got} (at character {t[2] + 1})")

    def name(self, name: str, pos: int):
        if name in _CONSTANTS:
            return ("lit", _CONSTANTS[name])
        if name == "_":
            if not self.under:
                raise RuleSyntaxError("`_` is only allowed in a rule with any_of")
            return ("under",)
        if name.startswith("patient."):
            field = name.split(".", 1)[1]
            if field not in self.patient_ids:
                raise RuleSyntaxError(f"unknown patient field {field!r} (at character {pos + 1})")
            return ("pref", field)
        if "." in name:
            raise RuleSyntaxError(f"unknown name {name!r} (at character {pos + 1})")
        if name in self.ids:
            return ("ref", name)
        return ("lit", name)  # a bare word: a string value, e.g. yes / worse

    def call(self):
        _, fname, pos = self.take("name")
        if fname not in _FUNCTIONS:
            raise RuleSyntaxError(
                f"unknown function {fname!r} (at character {pos + 1}); "
                f"use {', '.join(sorted(_FUNCTIONS))}"
            )
        self.take("punct", "(")
        args: list = []
        while not (self.peek()[0] == "punct" and self.peek()[1] == ")"):
            if args:
                self.take("punct", ",")
            t = self.peek()
            if t[0] == "punct" and t[1] == "[":
                args.append(("list", self.list_()))
            elif t[0] == "name":
                self.i += 1
                args.append(("word", t[1]))
            elif t[0] == "lit":
                self.i += 1
                args.append(("word", t[1]))
            else:
                raise RuleSyntaxError(f"bad argument to {fname}() (at character {t[2] + 1})")
        self.take("punct", ")")
        return self._check_call(fname, args, pos)

    def _check_call(self, fname: str, args: list, pos: int):
        where = f"{fname}() at character {pos + 1}"
        if fname == "answered":
            if len(args) != 1 or args[0][0] != "word":
                raise RuleSyntaxError(f"{where}: takes one question id")
            qid = args[0][1]
            if qid == "_" and self.under:
                return ("answered", None)
            if qid not in self.ids:
                raise RuleSyntaxError(f"{where}: unknown question {qid!r}")
            return ("answered", qid)
        if fname == "mentions":
            if len(args) != 2 or args[0][0] != "word" or args[1][0] != "list":
                raise RuleSyntaxError(f"{where}: takes a question id and a [list of words]")
            qid = args[0][1]
            if qid == "_" and not self.under:
                raise RuleSyntaxError(f"{where}: `_` is only allowed in a rule with any_of")
            if qid != "_" and qid not in self.ids:
                raise RuleSyntaxError(f"{where}: unknown question {qid!r}")
            words = [str(w) for w in args[1][1]]
            if not words or not all(w.strip() for w in words):
                raise RuleSyntaxError(f"{where}: give at least one non-empty word")
            return ("mentions", None if qid == "_" else qid, tuple(words))
        # check(name, args...)
        if not args or args[0][0] != "word":
            raise RuleSyntaxError(f"{where}: takes a check name")
        name = str(args[0][1])
        rest = tuple(str(a[1]) for a in args[1:] if a[0] == "word")
        if len(rest) != len(args) - 1:
            raise RuleSyntaxError(f"{where}: check arguments are names, not lists")
        problem = check_call_problem(name, rest, self.ids)
        if problem:
            raise RuleSyntaxError(f"{where}: {problem}")
        return ("check", name, rest)

    def list_(self) -> tuple:
        self.take("punct", "[")
        items: list = []
        while not (self.peek()[0] == "punct" and self.peek()[1] == "]"):
            if items:
                self.take("punct", ",")
            t = self.peek()
            if t[0] == "lit":
                items.append(t[1])
            elif t[0] == "name":
                items.append(_CONSTANTS.get(t[1], t[1]) if t[1] in _CONSTANTS else t[1])
            else:
                raise RuleSyntaxError(f"bad list item (at character {t[2] + 1})")
            self.i += 1
        self.take("punct", "]")
        return tuple(items)


def check_call_problem(name: str, args: tuple[str, ...], ids: frozenset[str]) -> str:
    """Why ``check(name, args)`` is not a valid call ("" if it is)."""
    from .checks import CHECK_ARG_VALUES

    if name not in NAMED_CHECKS:
        return f"unknown named check {name!r} (known: {', '.join(sorted(NAMED_CHECKS))})"
    _fn, n, _doc = NAMED_CHECKS[name]
    if len(args) != n:
        return f"check {name!r} takes {n} argument(s), got {len(args)}"
    allowed = CHECK_ARG_VALUES.get(name)
    for a in args:
        if allowed is not None:
            if a not in allowed:
                return f"check {name!r}: argument {a!r} must be one of {', '.join(sorted(allowed))}"
        elif a not in ids:
            return f"check {name!r}: argument {a!r} is not a question id"
    return ""


@lru_cache(maxsize=4096)
def compile_expr(
    text: str, ids: frozenset[str], patient_ids: frozenset[str] = frozenset(), under=False
):
    """Parse ``text`` into a small tuple tree. Raises RuleSyntaxError."""
    return _Parser(text, ids, patient_ids, under).parse()


# =============================================================================
# Evaluation
# =============================================================================


def _is_number(v: Any) -> bool:
    # bool counts as a number, as in Python (and as in the pre-engine code).
    return isinstance(v, int | float)


def typed_view(c: Campaign, answers: dict) -> dict[str, Any]:
    """The answers as rules see them: a scale answer counts only as a whole number,
    a number answer only as a number."""
    view: dict[str, Any] = {}
    for q in c.questions:
        v = answers.get(q.id)
        if q.kind == "scale":
            v = v if isinstance(v, int) else None
        elif q.kind == "number":
            v = v if _is_number(v) else None
        view[q.id] = v
    return view


def _answered(c: Campaign, qid: str, view: dict) -> bool:
    q = c.question(qid)
    v = view.get(qid)
    if q is not None and q.kind in ("scale", "number"):
        return v is not None
    return bool(str(v or "").strip())


def _mentions(value: Any, words: tuple[str, ...]) -> bool:
    text = str(value or "")
    t = _norm(text)
    if not t:
        return False
    negated = _negated_words(text)
    for w in words:
        key = _norm(w)
        if key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", t):
            if not any(part in negated for part in key.split()):
                return True
    return False


@dataclass
class _Env:
    campaign: Campaign
    view: dict
    patient: dict
    ctx: CheckContext
    under: str | None = None


def _patient_value(env: _Env, field: str) -> Any:
    v = env.patient.get(field)
    pf = env.campaign.patient_field(field)
    if pf is not None and pf.type == "bool":
        return coerce_bool(v) if v is not None else False
    return v


def _value(node, env: _Env) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "ref":
        return env.view.get(node[1])
    if kind == "under":
        return env.view.get(env.under) if env.under else None
    if kind == "pref":
        return _patient_value(env, node[1])
    return _truth(node, env)


def _truth(node, env: _Env) -> bool:
    kind = node[0]
    if kind == "or":
        return any(_truth(n, env) for n in node[1])
    if kind == "and":
        return all(_truth(n, env) for n in node[1])
    if kind == "not":
        return not _truth(node[1], env)
    if kind == "cmp":
        op, a, b = node[1], _value(node[2], env), _value(node[3], env)
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if not (_is_number(a) and _is_number(b)):
            return False
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if kind == "in":
        v = _value(node[1], env)
        hit = v in node[2]
        return not hit if node[3] else hit
    if kind == "answered":
        return _answered(env.campaign, node[1] or env.under or "", env.view)
    if kind == "mentions":
        qid = node[1] or env.under
        return _mentions(env.view.get(qid) if qid else None, node[2])
    if kind == "check":
        return bool(run_check(node[1], env.ctx, node[2]))
    if kind in ("lit", "ref", "under", "pref"):
        return bool(_value(node, env))
    raise RuleSyntaxError(f"cannot evaluate {kind}")  # pragma: no cover - parser bug


def _patient_ids(c: Campaign) -> frozenset[str]:
    return frozenset(f.id for f in c.patient_fields)


def compile_rule_when(c: Campaign, text: str, *, under: bool = False):
    return compile_expr(text, frozenset(c.question_ids), _patient_ids(c), under)


#: A finding's text is stored as a follow_up_reasons entry (reports.MAX_REASON_CHARS).
MAX_FINDING_CHARS = 200


def _format_reason(template: str, view: dict) -> str:
    """The reason with {question_id} filled from the answers, cut to fit a report."""
    out = []
    for lit, field, _spec, _conv in string.Formatter().parse(template):
        out.append(lit)
        if field is not None:
            v = view.get(field)
            out.append("" if v is None else str(v))
    return "".join(out)[:MAX_FINDING_CHARS]


def _rule_findings(c: Campaign, rule, view: dict, ctx: CheckContext, patient: dict):
    """(tier, text, fields) for each finding this rule adds."""
    env = _Env(c, view, patient, ctx)
    if rule.any_of:
        node = compile_rule_when(c, rule.when, under=True) if rule.when else None
        matched = []
        for qid in rule.any_of:
            env.under = qid
            if node is None or _truth(node, env):
                matched.append(qid)
        if not matched:
            return []
        return [(rule.tier, _format_reason(rule.reason, view), tuple(matched))]
    if rule.when and not _truth(compile_rule_when(c, rule.when), env):
        return []
    fields = tuple(rule.fields)
    reason = _format_reason(rule.reason, view)
    if not rule.check:
        return [(rule.tier, reason, fields)]
    result = run_check(rule.check, ctx, rule.args)
    if not result:
        return []
    hits = result if isinstance(result, list) else [result]
    out = []
    for h in hits:
        if isinstance(h, Hit):
            out.append(
                (
                    rule.tier,
                    h.text if h.text is not None else reason,
                    h.fields if h.fields is not None else fields,
                )
            )
        elif h:
            out.append((rule.tier, reason, fields))
    return out


def evaluate(c: Campaign, answers: dict, patient: dict | None = None) -> Triage:
    """Every rule over the answers; the most urgent tier wins.

    ``follow_up_reason`` names that tier's findings first, then the lower ones
    ("Also noted: ..."). It may reference ONLY what the patient reported in this
    conversation -- rules' reasons format answers, never patient fields.
    """
    patient = patient or {}
    view = typed_view(c, answers)
    ctx = CheckContext(c, answers, patient)
    found: list[tuple[str, str, tuple[str, ...]]] = []
    for rule in c.rules:
        found.extend(_rule_findings(c, rule, view, ctx, patient))
    rank = {t: i for i, t in enumerate(TIER_ORDER)}
    found.sort(key=lambda f: rank.get(f[0], len(rank)))  # stable: rule order within a tier
    return combine(found)


def combine(found: list[tuple[str, str, tuple[str, ...]]]) -> Triage:
    """The Triage for findings already ordered most urgent first."""
    if not found:
        return Triage(FOLLOW_UP_NONE, NO_FINDINGS_REASON)
    top = next(tier for tier in TIER_ORDER if any(f[0] == tier for f in found))
    top_texts = [text for tier, text, _ in found if tier == top]
    reason = "Patient reports " + ", ".join(top_texts) + "."
    lower = [(tier, text) for tier, text, _ in found if tier != top]
    if lower:
        reason += " Also noted: " + "; ".join(
            f"{text} ({TIER_SHORT[tier]})" for tier, text in lower
        )
        reason += "."
    triggers = tuple(dict.fromkeys(f for tier, _, fields in found if tier == top for f in fields))
    findings = tuple(
        {"tier": tier, "text": text, "fields": list(fields), "source": "triage"}
        for tier, text, fields in found
    )
    return Triage(top, reason, triggers, findings)


# =============================================================================
# Spec tests
# =============================================================================


def spec_test_failures(c: Campaign) -> list[str]:
    """Run the spec's own ``tests`` through the engine; one line per failure."""
    out = []
    for t in c.tests:
        try:
            r = evaluate(c, dict(t.answers), dict(t.patient))
        except Exception as e:  # a spec bug, reported as one
            out.append(f"test {t.name!r}: the rules raised {type(e).__name__}: {e}")
            continue
        texts = [f["text"] for f in r.findings]
        if r.follow_up != t.expect.tier:
            out.append(
                f"test {t.name!r}: expected tier {t.expect.tier}, got {r.follow_up} ({r.reason})"
            )
        for want in t.expect.reasons_include:
            if not any(want in x for x in texts):
                out.append(f"test {t.name!r}: no finding mentions {want!r} (got {texts})")
        for unwanted in t.expect.reasons_exclude:
            if any(unwanted in x for x in texts):
                out.append(f"test {t.name!r}: a finding mentions {unwanted!r} (got {texts})")
        if t.expect.reasons is not None and texts != t.expect.reasons:
            out.append(f"test {t.name!r}: expected findings {t.expect.reasons}, got {texts}")
    return out
