"""Use-case specs (``config/campaigns/<id>.yaml``), schema v2.

One spec describes one kind of outreach: who it is for, what it asks and in what
order, how the answers are triaged, and what the voice agent and the SMS
questionnaire say. The code is a generic engine that reads it
(docs/USE-CASES-PLAN.md):

* parsing by question TYPE (``voa_core.parsers``), with named parsers for fields
  whose wording needs reviewed code;
* triage by ``rules`` (``voa_core.rules``): a small safe expression language plus
  named, reviewed Python checks (``voa_core.checks``);
* completeness ("is this report done?") from the questions themselves;
* the SMS wording (``Question.sms`` and its variants, ``Campaign.sms_scripts``),
  formatted by ``voa_core.copy``.

Every spec is checked by ``voa_core.campaign_lint.campaign_problems``. What a spec
can never turn off (identity check, consent, the shared red flags and the 911
script, no medical advice, calling hours, STOP/HELP) lives in code.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import ConfigError, find_config_dir

#: open_questions: a yes/no ("any questions?") followed by a loop that records each
#: question the patient asks (voice: record_patient_question; SMS: the survey).
#: scale_0_10 is an alias for ``scale`` with min 0 and max 10.
QuestionType = Literal[
    "text",
    "yes_no",
    "yes_no_unknown",
    "scale",
    "scale_0_10",
    "choice",
    "number",
    "date",
    "time",
    "open_questions",
]
PatientFieldType = Literal["text", "bool", "date", "time", "number", "choice"]
Channel = Literal["voice", "sms"]
Status = Literal["draft", "in_review", "approved", "active", "retired"]
_YES = {"yes", "y", "yeah", "yep", "true"}

#: A campaign id: a safe file stem (it names a file under config/campaigns).
CAMPAIGN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DEFAULT_CAMPAIGN_ID = "post_op"

#: The red flags every use case checks, every turn (the voice frame's safety rule).
#: A spec may ADD to them (``red_flags_extra``) but never remove one; changing this
#: list is a clinical change.
SHARED_RED_FLAGS: tuple[str, ...] = (
    "chest pain, pressure, tightness or squeezing",
    "trouble breathing or shortness of breath at rest",
    "fainting or passing out",
    "heavy or uncontrolled bleeding",
    "coughing up or vomiting blood, or bloody stool",
    'a sudden, severe ("worst ever") headache',
    "signs of stroke: face drooping, arm weakness, slurred speech",
    "calf swelling with pain",
    "thoughts of harming themselves",
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RequiredWith(_Spec):
    """Sub-answers a report must carry when the parent question has ``answer``."""

    answer: str = "yes"
    fields: list[str]
    #: key: the field must be present (any value); non_empty: present and truthy.
    presence: Literal["key", "non_empty"] = "non_empty"
    #: The report error, read by the voice model: say what to ask. {missing} lists
    #: the missing fields.
    message: str


class Question(_Spec):
    id: str
    ask: str
    type: QuestionType
    choices: list[str] = Field(default_factory=list)
    #: scale (and number) bounds. scale_0_10 implies 0 and 10.
    min: int | None = None
    max: int | None = None
    unit: str = ""
    #: A named parser (voa_core.parsers.NAMED_PARSERS) instead of the type's own.
    parser: str = ""
    #: Not needed for a report to be complete.
    optional: bool = False
    required_with: RequiredWith | None = None
    #: A named SMS follow-up helper (survey.FOLLOWUP_REUSE) that rewords this
    #: follow-up from what the patient already said, and selects the variants.
    reuse: str = ""
    when: dict[str, str] = Field(default_factory=dict)  # earlier answer conditions
    when_patient: dict[str, Any] = Field(default_factory=dict)  # patient-context conditions
    note: str = ""
    # SMS prompt. Required for every question when the spec has the sms channel.
    sms: str = ""
    sms_if_known: str = ""  # reuse pain_site: the patient already named a site ({site})
    sms_if_side_known: str = ""  # reuse swelling_parts: side said, ask only the trend
    sms_if_trend_known: str = ""  # reuse swelling_parts: trend said, ask only the side

    @model_validator(mode="after")
    def _scale_alias(self) -> Question:
        if self.type == "scale_0_10" and (self.min, self.max) not in ((None, None), (0, 10)):
            raise ValueError(f"{self.id}: scale_0_10 is always 0 to 10; use type scale")
        return self

    @property
    def kind(self) -> str:
        """The type with aliases resolved (scale_0_10 -> scale)."""
        return "scale" if self.type == "scale_0_10" else self.type

    def scale_range(self) -> tuple[int, int]:
        if self.type == "scale_0_10":
            return 0, 10
        lo = self.min if self.min is not None else 0
        hi = self.max if self.max is not None else 10
        return lo, hi


class PatientField(_Spec):
    id: str
    type: PatientFieldType = "text"
    required: bool = False
    choices: list[str] = Field(default_factory=list)


class Rule(_Spec):
    """One triage rule: when ``when`` holds (and the named ``check`` passes), add a
    finding at ``tier`` with ``reason`` (``{question_id}`` placeholders allowed)."""

    name: str = ""
    when: str = ""
    #: A named, reviewed Python check (voa_core.checks.NAMED_CHECKS), with ``args``.
    check: str = ""
    args: list[str] = Field(default_factory=list)
    #: Evaluate ``when`` once per id, with ``_`` standing for that answer; one
    #: finding whose fields are the ids that matched.
    any_of: list[str] = Field(default_factory=list)
    tier: str
    reason: str = ""
    fields: list[str] = Field(default_factory=list)


class SpecTestExpect(_Spec):
    tier: str
    reasons_include: list[str] = Field(default_factory=list)
    reasons_exclude: list[str] = Field(default_factory=list)
    #: The exact finding texts, in order (optional).
    reasons: list[str] | None = None


class SpecTest(_Spec):
    name: str
    patient: dict[str, Any] = Field(default_factory=dict)
    answers: dict[str, Any]
    expect: SpecTestExpect


class DisplayFinding(_Spec):
    label: str
    from_: str | list[str] = Field(alias="from")


class Display(_Spec):
    """What nurses see. Schema only in step 1 (rendered from it in step 2)."""

    title: str = ""
    facts: list[str] = Field(default_factory=list)
    findings: list[DisplayFinding] = Field(default_factory=list)


class Analytics(_Spec):
    """Schema only in step 1 (``fact_answers`` in step 2)."""

    measures: list[str] = Field(default_factory=list)


class Knowledge(_Spec):
    scope: str = ""
    context: list[str] = Field(default_factory=list)


class Campaign(_Spec):
    id: str
    name: str
    version: int = Field(default=1, ge=1)
    status: Status = "active"
    purpose: str = ""
    channels: list[Channel] = Field(default_factory=lambda: ["voice", "sms"])
    agent_name: str = "Sara"
    patient_fields: list[PatientField] = Field(default_factory=list)
    scripts: dict[str, str]
    questions: list[Question]
    rules: list[Rule] = Field(default_factory=list)
    #: None: the shared list (SHARED_RED_FLAGS). A spec that lists them must keep all.
    red_flags: list[str] | None = None
    red_flags_extra: list[str] = Field(default_factory=list)
    custom_vocabulary: list[str] = Field(default_factory=list)
    #: The SMS questionnaire's own lines (preamble, questions loop, closings).
    sms_scripts: dict[str, str] = Field(default_factory=dict)
    #: How the voice frame names ``scripts.closing_extra`` ("the medication-list
    #: reminder"); empty: not mentioned.
    closing_extra_label: str = ""
    #: The word in report errors ("Ask every applicable <noun> question").
    question_noun: str = ""
    display: Display = Field(default_factory=Display)
    analytics: Analytics = Field(default_factory=Analytics)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    tests: list[SpecTest] = Field(default_factory=list)

    # ------------------------------------------------------------------ lookups
    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions)

    @property
    def all_red_flags(self) -> list[str]:
        base = list(SHARED_RED_FLAGS) if self.red_flags is None else list(self.red_flags)
        return base + [f for f in self.red_flags_extra if f not in base]

    @property
    def has_open_questions(self) -> bool:
        return any(q.kind == "open_questions" for q in self.questions)

    @property
    def open_question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions if q.kind == "open_questions")

    @property
    def closing_extra(self) -> str:
        """Said before the voice closing (post-op: the medication-list reminder)."""
        return self.scripts.get("closing_extra", "")

    def patient_field(self, fid: str) -> PatientField | None:
        return next((f for f in self.patient_fields if f.id == fid), None)

    def report_required(self) -> list[str]:
        """Answers a completed report must carry: every top-level question that is
        not optional. Patient-conditional questions are not required here (report
        validation has no patient context; the voice tool asks them)."""
        return [
            q.id for q in self.questions if not q.when and not q.when_patient and not q.optional
        ]

    # ------------------------------------------------------------------ flow
    def for_patient(self, patient: dict[str, Any]) -> list[Question]:
        """Questions this patient can be asked (patient-context conditions only)."""
        return [
            q
            for q in self.questions
            if all(norm_answer(patient.get(k)) == norm_answer(v) for k, v in q.when_patient.items())
        ]

    def applicable(self, patient: dict[str, Any], answers: dict[str, Any]) -> list[Question]:
        """Questions that apply to this patient given the answers so far."""
        out = []
        for q in self.questions:
            if any(
                norm_answer(patient.get(k)) != norm_answer(v) for k, v in q.when_patient.items()
            ):
                continue
            if any(norm_answer(answers.get(k)) != norm_answer(v) for k, v in q.when.items()):
                continue
            out.append(q)
        return out

    def missing(self, patient: dict[str, Any], answers: dict[str, Any]) -> list[str]:
        return [
            q.id
            for q in self.applicable(patient, answers)
            if not str(answers.get(q.id, "")).strip()
        ]

    def script(self, key: str, **values: str) -> str:
        return self.scripts[key].format(**values)


def norm_answer(v: Any) -> str:
    """Compare answers and flags loosely: True/"true"/"yeah" -> "yes", False/"false" -> "no"."""
    s = str(v).strip().lower()
    if s in _YES:
        return "yes"
    if s in {"no", "n", "nope", "false"}:
        return "no"
    return s


class UnknownCampaign(ValueError):
    """No spec file for this id (or the id is not a valid one)."""


def default_config_dir() -> Path:
    try:
        return find_config_dir()
    except ConfigError:
        # Running outside the repo root (and without VOA_CONFIG_DIR): look up
        # from this file, which finds the workspace's config/ in development.
        return find_config_dir(Path(__file__).parent)


def campaign_path(campaign_id: str, config_dir: Path | None = None) -> Path:
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.match(campaign_id):
        raise UnknownCampaign(f"not a valid campaign id: {campaign_id!r}")
    return (config_dir or default_config_dir()) / "campaigns" / f"{campaign_id}.yaml"


def campaign_ids(config_dir: Path | None = None) -> list[str]:
    """Every spec under config/campaigns, by id."""
    d = (config_dir or default_config_dir()) / "campaigns"
    return sorted(p.stem for p in d.glob("*.yaml") if CAMPAIGN_ID_RE.match(p.stem))


@cache
def load_campaign(campaign_id: str, config_dir: Path | None = None) -> Campaign:
    path = campaign_path(campaign_id, config_dir)
    if not path.is_file():
        raise UnknownCampaign(f"no campaign {campaign_id!r}")
    c = Campaign.model_validate(yaml.safe_load(path.read_text()))
    if c.id != campaign_id:
        raise UnknownCampaign(f"{path.name} declares id {c.id!r}")
    return c


def campaign_exists(campaign_id: str, config_dir: Path | None = None) -> bool:
    try:
        load_campaign(campaign_id, config_dir)
    except (OSError, ValueError):
        return False
    return True


def validate_campaign_id(value: Any) -> str | None:
    """For API models: None or "" passes (the default applies); anything else must
    name a loadable spec. Raises ValueError (pydantic turns it into a 422)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not campaign_exists(value.strip()):
        raise ValueError("campaign_id is not a known use case")
    return value.strip()
