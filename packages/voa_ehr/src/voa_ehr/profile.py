"""The EHR export profile: how a signed-off call becomes FHIR R4 resources.

A profile is data (YAML or JSON) that an admin edits, in the console or in git.
It never holds a credential (only ``secret://name`` references) and never holds
code: every transform is one of a small, fixed set (docs/EHR-EXPORT-PLAN.md §2).

Two layers of checking, like the use-case specs:

* the pydantic model (``extra="forbid"`` everywhere, so a misspelt key is an
  error, not a silently ignored setting);
* :func:`profile_problems`, which checks the profile against the use-case specs
  it names (``config/campaigns/<id>.yaml``): every question id exists, every
  answer type suits the question, every code map covers the question's choices.

:func:`load_profile` does both and raises :class:`ProfileError` on any error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from voa_core.campaign import Campaign, UnknownCampaign, load_campaign
from voa_core.review import TIER_RANK

PROFILE_SCHEMA = 1

#: secret://name[#version], the repo-wide Secret Manager reference (voa_core.config).
SECRET_REF_RE = re.compile(r"^secret://[A-Za-z0-9_-]{1,255}(#[A-Za-z0-9_-]{1,64})?$")
#: A FHIR id / identifier-ish token we allow in systems and link ids.
LINK_ID_RE = re.compile(r"^[A-Za-z0-9._\-/:]{1,255}$")
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
#: strptime directives a date transform may use: nothing else.
_DATE_DIRECTIVES = set("YmdyBbj")

AnswerType = Literal["boolean", "integer", "decimal", "string", "coding", "date", "quantity"]
TaskStatus = Literal[
    "draft",
    "requested",
    "received",
    "accepted",
    "rejected",
    "ready",
    "cancelled",
    "in-progress",
    "on-hold",
    "failed",
    "completed",
]
TaskPriority = Literal["routine", "urgent", "asap", "stat"]

#: Answer keys the engine writes besides the question ids (voa_core.survey).
ENGINE_FIELDS: dict[str, str] = {
    "blood_sugar_reading": "text",  # the patient's own words (blood_sugar parser)
    "patient_questions": "questions",  # [{text, category, priority}] (open_questions)
}

#: Which FHIR answer types suit which question kind. A mapping outside this is an
#: error ("bad type"): an integer from a yes/no question can only be wrong.
TYPE_COMPAT: dict[str, frozenset[str]] = {
    "yes_no": frozenset({"boolean", "coding", "string"}),
    "yes_no_unknown": frozenset({"boolean", "coding", "string"}),
    "scale": frozenset({"integer", "decimal", "quantity", "coding", "string"}),
    "number": frozenset({"integer", "decimal", "quantity", "string"}),
    "choice": frozenset({"coding", "string"}),
    "text": frozenset({"string"}),
    # A text question with a named parser stores a category (blood sugar: high,
    # low, in_range), so a code map makes sense.
    "text_parsed": frozenset({"string", "coding"}),
    "date": frozenset({"date", "string"}),
    "time": frozenset({"string"}),
    "open_questions": frozenset({"boolean", "coding", "string"}),
    "questions": frozenset({"string"}),
}


class ProfileError(ValueError):
    """The profile cannot be used. ``problems`` lists every error found."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("; ".join(f"{p.path}: {p.message}" for p in problems if p.is_error))


@dataclass(frozen=True)
class Problem:
    level: Literal["error", "warning"]
    path: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "path": self.path, "message": self.message}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- target


def _secret_ref(v: str) -> str:
    if v and not SECRET_REF_RE.match(v):
        raise ValueError("must be a Secret Manager reference: secret://<name>[#version]")
    return v


class Auth(_M):
    """How the exporter authenticates. Credentials are secret references only.

    ``none`` and ``bearer`` are implemented; the OAuth modes are in the schema so a
    profile can be written for them now, and are refused by the exporter until
    they are built (plan §6).
    """

    type: Literal["none", "bearer", "client_credentials", "smart_backend"] = "none"
    #: bearer: the token. client_credentials: the client secret.
    #: smart_backend: the private key (PEM) that signs the client assertion.
    secret: str = ""
    client_id: str = ""
    token_url: str = ""
    scope: str = ""

    @field_validator("secret")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        return _secret_ref(v)

    @model_validator(mode="after")
    def _needs(self) -> Auth:
        if self.type != "none" and not self.secret:
            raise ValueError(f"auth type {self.type} needs a secret reference")
        if self.type in ("client_credentials", "smart_backend") and not (
            self.client_id and self.token_url
        ):
            raise ValueError(f"auth type {self.type} needs client_id and token_url")
        return self


class Target(_M):
    fhir_version: Literal["R4"] = "R4"
    #: The FHIR base, e.g. https://fhir.example.org/R4. Not a secret; https only
    #: (http is accepted for localhost, for the local fake server).
    base_url: str
    auth: Auth = Field(default_factory=Auth)
    timeout_s: float = Field(default=30, gt=0, le=120)
    #: transaction: one Bundle per call (all or nothing). individual: one request
    #: per resource, for servers without transaction support (common with
    #: vendor EHRs; references are filled in from each response).
    submit: Literal["transaction", "individual"] = "transaction"
    #: conditional_update: PUT Type?identifier=... (a re-sign-off updates the same
    #: resource). conditional_create: POST with If-None-Exist (never updates).
    write_mode: Literal["conditional_update", "conditional_create"] = "conditional_update"

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        u = urlparse(v)
        local = u.hostname in ("localhost", "127.0.0.1", "::1")
        if u.scheme != "https" and not (u.scheme == "http" and local):
            raise ValueError("base_url must be https (http only for localhost)")
        if not u.hostname or u.query or u.fragment or u.username or u.password:
            raise ValueError("base_url must be a plain https URL with no query or credentials")
        return v.rstrip("/")


# --------------------------------------------------------------------------- patient


class PatientLookup(_M):
    """Patient?identifier=<system>|<mrn>. Never creates a patient."""

    identifier_system: str
    #: retry: leave the record pending (the EHR may not have the patient yet) until
    #: max_attempts, then failed. fail: failed at once. skip: status skipped.
    not_found: Literal["retry", "fail", "skip"] = "retry"
    #: Add birthdate=<dob> to the search (the call's DOB is read ONLY when true).
    #: Off by default: the MRN is the match; the DOB is PHI we do not otherwise send.
    search_with_birthdate: bool = False
    #: Put the patient's name in Reference.display. Off by default.
    include_display_name: bool = False


class Identifiers(_M):
    """Business identifiers that make every write idempotent.

    Each resource gets ``{system}|{call_id}`` (QuestionnaireResponse) or
    ``{system}|{call_id}-<suffix>`` (Task, each Observation, documents).
    """

    system: str


# --------------------------------------------------------------------------- values


class Coding(_M):
    code: str
    display: str = ""
    #: Overrides the map's system for this code.
    system: str = ""


class ValueSpec(_M):
    """How one app answer becomes one FHIR value[x]. Transforms run in order:
    constant | concat -> value_map -> type conversion."""

    type: AnswerType
    #: coding: the code system, and app value -> code. Keys are matched after
    #: normalising the answer (lower case; yes/true/yeah -> yes; no/false -> no).
    system: str = ""
    codes: dict[str, Coding] = Field(default_factory=dict)
    #: Enum -> enum before typing, e.g. {better: improving}.
    value_map: dict[str, str | int | float | bool] = Field(default_factory=dict)
    #: Send this value whatever was answered (only when the question was answered).
    constant: str | int | float | bool | None = None
    #: Join several answers into one string (ids; missing ones are left out).
    concat: list[str] = Field(default_factory=list)
    separator: str = "; "
    #: date: input formats tried in order (strptime, date directives only).
    date_formats: list[str] = Field(default_factory=lambda: ["%Y-%m-%d"])
    #: quantity: the unit (UCUM by default).
    unit: str = ""
    unit_system: str = "http://unitsofmeasure.org"
    unit_code: str = ""

    @model_validator(mode="after")
    def _shape(self) -> ValueSpec:
        if self.type == "coding":
            if not self.codes:
                raise ValueError("type coding needs codes (app value -> code)")
            missing = [k for k, c in self.codes.items() if not (c.system or self.system)]
            if missing:
                raise ValueError(f"coding needs a system (map-level or per code): {missing}")
        elif self.codes or self.system:
            raise ValueError("codes/system are only for type coding")
        if self.unit and self.type != "quantity":
            raise ValueError("unit is only for type quantity (use quantity for a value with units)")
        if self.type == "quantity" and not self.unit:
            raise ValueError("type quantity needs a unit")
        if self.concat and self.type != "string":
            raise ValueError("concat produces text: use type string")
        for f in self.date_formats:
            ds = re.findall(r"%(.)", f)
            if not ds or not set(ds) <= _DATE_DIRECTIVES:
                raise ValueError(f"date format {f!r}: only %Y %m %d %y %b %B %j are allowed")
        return self


class ItemMap(ValueSpec):
    """One question -> one QuestionnaireResponse item."""

    link_id: str
    text: str = ""

    @field_validator("link_id")
    @classmethod
    def _lid(cls, v: str) -> str:
        if not LINK_ID_RE.match(v):
            raise ValueError("link_id: letters, digits and . _ - / : only")
        return v


class QuestionnaireResponseMap(_M):
    enabled: bool = True
    #: The canonical URL of the EHR's Questionnaire (optionally |version).
    questionnaire: str
    #: Answers with no mapping: skip them, or send them as text items under
    #: ``unmapped_prefix + question id`` (only if the EHR's Questionnaire has them).
    unmapped: Literal["skip", "text"] = "skip"
    unmapped_prefix: str = ""
    #: Never sent, mapped or not.
    exclude: list[str] = Field(default_factory=list)
    author_display: str = "Automated outreach (patient-reported)"
    items: dict[str, ItemMap] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> QuestionnaireResponseMap:
        seen: dict[str, str] = {}
        for qid, m in self.items.items():
            if m.link_id in seen:
                raise ValueError(f"link_id {m.link_id!r} used by {seen[m.link_id]} and {qid}")
            seen[m.link_id] = qid
        return self


DEFAULT_PRIORITY: dict[str, str] = {
    "emergency": "stat",
    "urgent_same_day": "urgent",
    "routine_next_business_day": "routine",
    "informational": "routine",
    "none": "routine",
}


class TaskOwner(_M):
    #: display: Reference.display = the signer. identifier: a logical reference
    #: {identifier: {system, value: signer}}. none: no owner.
    mode: Literal["none", "display", "identifier"] = "display"
    identifier_system: str = ""

    @model_validator(mode="after")
    def _sys(self) -> TaskOwner:
        if self.mode == "identifier" and not self.identifier_system:
            raise ValueError("owner mode identifier needs identifier_system")
        return self


class TaskMap(_M):
    """The nurse follow-up as a Task."""

    enabled: bool = True
    #: resolved: when the sign-off resolved a follow-up (today's outbox ``task``).
    #: needs_follow_up: when the tier needed a callback. always: every sign-off.
    emit_when: Literal["resolved", "needs_follow_up", "always"] = "resolved"
    #: Task.status by state: resolved (the nurse closed the follow-up) / open.
    status: dict[Literal["resolved", "open"], TaskStatus] = Field(
        default_factory=lambda: {"resolved": "completed", "open": "requested"}
    )
    intent: Literal["order", "plan", "proposal"] = "order"
    #: Task.priority from the call's tier.
    priority: dict[str, TaskPriority] = Field(default_factory=lambda: dict(DEFAULT_PRIORITY))
    code: Coding | None = None
    code_system: str = ""
    description: str = "RN follow-up after automated outreach"
    include_nurse_note: bool = True
    include_reason: bool = True
    owner: TaskOwner = Field(default_factory=TaskOwner)

    @field_validator("priority")
    @classmethod
    def _tiers(cls, v: dict[str, str]) -> dict[str, str]:
        bad = sorted(set(v) - set(TIER_RANK))
        if bad:
            raise ValueError(f"unknown tiers {bad}; tiers are {sorted(TIER_RANK)}")
        return v

    @model_validator(mode="after")
    def _code(self) -> TaskMap:
        if self.code and not (self.code.system or self.code_system):
            raise ValueError("task code needs a system")
        return self


class ObservationMap(ValueSpec):
    """One answer -> one Observation (e.g. a pain score with a LOINC code)."""

    source: str
    code: Coding
    code_system: str = ""
    category: Literal["survey", "vital-signs", "exam", "social-history"] = "survey"

    @model_validator(mode="after")
    def _code_sys(self) -> ObservationMap:
        if not (self.code.system or self.code_system):
            raise ValueError("observation code needs a system")
        return self


class DocumentMap(_M):
    """A summary or the transcript as a DocumentReference (or Communication).

    Off by default. Both carry PHI in free text: turning one on is the deploying
    organisation's privacy decision, recorded as ``phi_acknowledged: true``.
    """

    enabled: bool = False
    resource: Literal["DocumentReference", "Communication"] = "DocumentReference"
    phi_acknowledged: bool = False
    type: Coding | None = None
    type_system: str = ""
    #: transcript only: original (as spoken) or english (the translation, when
    #: there is one; else the original).
    language: Literal["original", "english"] = "original"

    @model_validator(mode="after")
    def _phi(self) -> DocumentMap:
        if self.enabled and not self.phi_acknowledged:
            raise ValueError(
                "this sends free-text PHI to the EHR: set phi_acknowledged: true "
                "once your privacy office has approved it"
            )
        if self.type and not (self.type.system or self.type_system):
            raise ValueError("document type needs a system")
        return self


class UseCaseMap(_M):
    """The mapping for one use case (``campaign_id``), optionally only for some
    versions of its spec. The most specific match wins (listed version over any)."""

    campaign_id: str
    #: Spec versions this mapping covers; empty = every version.
    spec_versions: list[int] = Field(default_factory=list)
    questionnaire_response: QuestionnaireResponseMap | None = None
    task: TaskMap | None = None
    observations: list[ObservationMap] = Field(default_factory=list)
    summary: DocumentMap = Field(default_factory=DocumentMap)
    transcript: DocumentMap = Field(default_factory=DocumentMap)

    def covers(self, version: int | None) -> bool:
        return not self.spec_versions or version in self.spec_versions


class Profile(_M):
    profile_schema: Literal[1] = PROFILE_SCHEMA
    id: str
    name: str
    #: The profile's own revision (draft -> active in the console).
    version: int = Field(default=1, ge=1)
    description: str = ""
    target: Target
    patient: PatientLookup
    identifiers: Identifiers
    use_cases: list[UseCaseMap]
    #: Validate every resource against FHIR R4 structure before sending.
    validate_output: bool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not PROFILE_ID_RE.match(v):
            raise ValueError("id: lower case letters, digits, . _ - (start with a letter)")
        return v

    @model_validator(mode="after")
    def _no_overlap(self) -> Profile:
        by: dict[str, list[UseCaseMap]] = {}
        for u in self.use_cases:
            for other in by.get(u.campaign_id, []):
                a, b = set(u.spec_versions), set(other.spec_versions)
                if (not a and not b) or (a & b):
                    raise ValueError(
                        f"two mappings for {u.campaign_id} cover the same spec versions"
                    )
            by.setdefault(u.campaign_id, []).append(u)
        return self

    def for_use_case(self, campaign_id: str | None, version: int | None) -> UseCaseMap | None:
        hits = [u for u in self.use_cases if u.campaign_id == campaign_id and u.covers(version)]
        hits.sort(key=lambda u: 0 if u.spec_versions else 1)
        return hits[0] if hits else None

    def secret_refs(self) -> list[str]:
        return [self.target.auth.secret] if self.target.auth.secret else []


# --------------------------------------------------------------------------- loading


def parse_profile(data: Any) -> Profile:
    """The model only (no spec checks). Raises ProfileError."""
    try:
        return Profile.model_validate(data)
    except ValidationError as e:
        raise ProfileError(
            [
                Problem("error", ".".join(str(x) for x in err["loc"]) or "profile", err["msg"])
                for err in e.errors()
            ]
        ) from None


def profile_from_text(text: str) -> Profile:
    """YAML or JSON (JSON is YAML). ``safe_load``: no tags, no objects."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ProfileError([Problem("error", "profile", f"not valid YAML/JSON: {e}")]) from None
    return parse_profile(data)


def profile_to_yaml(profile: Profile) -> str:
    return yaml.safe_dump(
        profile.model_dump(mode="json", exclude_defaults=True), sort_keys=False, allow_unicode=True
    )


def profile_to_json(profile: Profile) -> str:
    return json.dumps(profile.model_dump(mode="json"), indent=2)


def load_profile(
    path: str | Path, *, config_dir: Path | None = None, check: bool = True
) -> Profile:
    """Read, parse and (by default) check a profile file. Raises ProfileError."""
    profile = profile_from_text(Path(path).read_text())
    if check:
        problems = profile_problems(profile, config_dir=config_dir)
        if any(p.is_error for p in problems):
            raise ProfileError(problems)
    return profile


# --------------------------------------------------------------------------- spec checks


def _kind(campaign: Campaign, qid: str) -> str | None:
    for q in campaign.questions:
        if q.id == qid:
            return "text_parsed" if q.kind == "text" and q.parser else q.kind
    return ENGINE_FIELDS.get(qid)


def _choices(campaign: Campaign, qid: str) -> list[str] | None:
    for q in campaign.questions:
        if q.id == qid:
            if q.kind == "yes_no":
                return ["yes", "no"]
            if q.kind == "yes_no_unknown":
                return ["yes", "no", "unknown"]
            if q.kind == "choice":
                return list(q.choices)
    return None


def _check_value(
    campaign: Campaign, qid: str, spec: ValueSpec, path: str, out: list[Problem]
) -> None:
    kind = _kind(campaign, qid)
    if kind is None:
        out.append(Problem("error", path, f"{qid!r} is not a question in {campaign.id}"))
        return
    for c in spec.concat:
        if _kind(campaign, c) is None:
            out.append(Problem("error", path, f"concat: {c!r} is not a question in {campaign.id}"))
    if spec.constant is None and not spec.concat:
        allowed = TYPE_COMPAT.get(kind, frozenset({"string"}))
        if spec.value_map:
            # A value map can turn any enum into anything; trust the admin's map.
            allowed = allowed | {"boolean", "integer", "decimal", "coding", "string"}
        if spec.type not in allowed:
            out.append(
                Problem(
                    "error",
                    path,
                    f"type {spec.type} does not fit {qid!r} (a {kind} question); "
                    f"use one of {sorted(allowed)}",
                )
            )
            return
    choices = _choices(campaign, qid)
    if spec.type == "coding" and choices and spec.constant is None:
        mapped = {spec.value_map.get(c, c) for c in choices}
        uncovered = [c for c in choices if str(spec.value_map.get(c, c)) not in spec.codes]
        if uncovered:
            out.append(
                Problem(
                    "warning",
                    path,
                    f"no code for answers {uncovered}: left out "
                    "(QuestionnaireResponse: per 'unmapped')",
                )
            )
        extra = sorted(set(spec.codes) - {str(m) for m in mapped})
        if extra:
            out.append(Problem("warning", path, f"codes {extra} match no answer of {qid!r}"))
    if spec.type == "boolean" and kind == "yes_no_unknown" and "unknown" not in spec.value_map:
        out.append(
            Problem(
                "warning",
                path,
                f"{qid!r} can be 'unknown', which is not a boolean: such an answer "
                "is left out (in a QuestionnaireResponse, handled per 'unmapped'); "
                "a coding keeps it",
            )
        )


def profile_problems(profile: Profile, *, config_dir: Path | None = None) -> list[Problem]:
    """Every problem with ``profile`` against the use-case specs it names."""
    out: list[Problem] = []
    if not profile.use_cases:
        out.append(Problem("error", "use_cases", "no use case is mapped"))
    for i, uc in enumerate(profile.use_cases):
        base = f"use_cases[{i}]"
        try:
            campaign = load_campaign(uc.campaign_id, config_dir)
        except (UnknownCampaign, OSError, ValueError) as e:
            out.append(Problem("error", f"{base}.campaign_id", f"unknown use case: {e}"))
            continue
        if uc.spec_versions and campaign.version not in uc.spec_versions:
            out.append(
                Problem(
                    "warning",
                    f"{base}.spec_versions",
                    f"the current {uc.campaign_id} spec is version {campaign.version}, "
                    f"not in {uc.spec_versions}: question ids are checked against it anyway",
                )
            )
        qr = uc.questionnaire_response
        if qr is not None:
            for qid, m in qr.items.items():
                _check_value(campaign, qid, m, f"{base}.questionnaire_response.items.{qid}", out)
            for qid in qr.exclude:
                if _kind(campaign, qid) is None:
                    out.append(
                        Problem(
                            "error",
                            f"{base}.questionnaire_response.exclude",
                            f"{qid!r} is not a question in {campaign.id}",
                        )
                    )
                if qid in qr.items:
                    out.append(
                        Problem(
                            "warning",
                            f"{base}.questionnaire_response.exclude",
                            f"{qid!r} is both mapped and excluded: it is not sent",
                        )
                    )
            if qr.enabled:
                unmapped = [
                    q.id
                    for q in campaign.questions
                    if q.id not in qr.items and q.id not in qr.exclude
                ]
                if unmapped:
                    out.append(
                        Problem(
                            "warning",
                            f"{base}.questionnaire_response.items",
                            f"not mapped ({qr.unmapped}): {unmapped}",
                        )
                    )
        seen_sources: set[str] = set()
        for j, ob in enumerate(uc.observations):
            path = f"{base}.observations[{j}]"
            _check_value(campaign, ob.source, ob, path, out)
            if ob.source in seen_sources:
                out.append(Problem("error", path, f"two observations from {ob.source!r}"))
            seen_sources.add(ob.source)
        if uc.observations and (qr is None or not qr.enabled):
            out.append(
                Problem(
                    "warning",
                    f"{base}.observations",
                    "observations without a QuestionnaireResponse have no derivedFrom link",
                )
            )
    return out
