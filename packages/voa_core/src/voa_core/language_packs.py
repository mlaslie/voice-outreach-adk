"""Language packs (docs/LANGUAGES-PLAN.md §3.2, step 4).

A pack is the approved wording of one use case in one language:
``config/campaigns/i18n/<campaign>.<lang>.yaml``. It is **wording only**: ids,
types, rules, triage, red flags and the call frame come from the English spec
(``config/campaigns/<campaign>.yaml``) and a pack cannot change them.

The file::

    language: es-US
    campaign: post_op
    status: draft            # draft | in_review | approved | retired
    translated_by: "..."
    reviewed_by: ""          # the qualified medical translator
    approved_by: ""          # the named approver
    approved_at: ""          # YYYY-MM-DD
    notes: "..."
    scripts:   {<every key of the spec's scripts>: "..."}
    questions: {<every question id>: {ask: "...", note: "..."}}
    custom_vocabulary: [...]  # optional: transcription hints in the language
    sms_scripts: {...}        # optional in step 4 (texts are step 5)
    # a question may also carry sms / sms_if_* wording (optional in step 4)

Lifecycle: a machine or a person drafts a pack, a qualified medical translator
reviews it (``reviewed_by``), and a named approver sets ``status: approved``
with ``approved_by`` and ``approved_at``. Only an approved pack whose wording is
complete and lint-clean makes a language *available* (``languages.available``).
For evals only, ``VOA_ALLOW_DRAFT_PACKS=1`` lets the gateway use a draft pack,
and only when ``VOA_ENV`` is ``dev`` or ``test`` (never in a deployed service).

``pack_problems`` is the lint; ``localize`` returns the English spec with the
pack's wording in place of the scripts and question wording.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .campaign import DEFAULT_CAMPAIGN_ID, Campaign, UnknownCampaign, load_campaign
from .copy import placeholders

log = logging.getLogger(__name__)

PackStatus = Literal["draft", "in_review", "approved", "retired"]
CHANNELS = ("voice", "sms")

#: Step 4 ships voice only. The shared SMS copy (invite, 1/2/3 menu, DOB prompt,
#: STOP/HELP replies: voa_core/copy.py) is not in the pack format yet (step 5),
#: so no non-English pack can be available for texts, whatever it contains.
SMS_PACKS_ENABLED = False

#: The eval-only switch, and the environments it is honoured in.
ALLOW_DRAFTS_ENV = "VOA_ALLOW_DRAFT_PACKS"
DRAFT_ENVS = frozenset({"dev", "test"})

_TOP_KEYS = frozenset(
    {
        "language",
        "campaign",
        "status",
        "translated_by",
        "reviewed_by",
        "approved_by",
        "approved_at",
        "notes",
        "scripts",
        "questions",
        "custom_vocabulary",
        "sms_scripts",
    }
)
_QUESTION_KEYS = ("ask", "note", "sms", "sms_if_known", "sms_if_side_known", "sms_if_trend_known")
_SMS_QUESTION_KEYS = _QUESTION_KEYS[2:]

#: The emergency line must name 911 and keep "hang up and call". Per primary
#: language tag: (ways to say 911, hang-up words, call words), accent-folded and
#: lower-cased. A language without an entry cannot pass the lint: adding one is
#: a developer's (and the translator's) job before that language goes live.
EMERGENCY_WORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "en": (("911", "nine one one"), ("hang up",), ("call",)),
    "es": (
        ("911", "nueve uno uno", "nueve, uno, uno", "nueve once"),
        ("cuelgue", "colgar", "cuelga"),
        ("llame", "llamar", "marque", "marcar"),
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackQuestion(_Strict):
    ask: str = ""
    #: Extra guidance for the voice agent (read by the model, never said as is).
    note: str = ""
    sms: str = ""
    sms_if_known: str = ""
    sms_if_side_known: str = ""
    sms_if_trend_known: str = ""


class Pack(_Strict):
    language: str
    campaign: str
    status: PackStatus = "draft"
    translated_by: str = ""
    reviewed_by: str = ""
    approved_by: str = ""
    approved_at: str = ""
    notes: str = ""
    scripts: dict[str, str] = Field(default_factory=dict)
    questions: dict[str, PackQuestion] = Field(default_factory=dict)
    custom_vocabulary: list[str] = Field(default_factory=list)
    sms_scripts: dict[str, str] = Field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status == "approved"


# --------------------------------------------------------------------------- files


def pack_path(campaign_id: str, language: str, config_dir: Path | None = None) -> Path:
    from . import languages

    return languages.pack_dir(config_dir) / f"{campaign_id}.{language}.yaml"


def _read(path: Path) -> Mapping[str, Any] | None:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, Mapping) else None


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime | date):
        return v.isoformat()
    return str(v)


def _model(doc: Mapping[str, Any]) -> Pack:
    data = dict(doc)
    for k in ("translated_by", "reviewed_by", "approved_by", "approved_at", "notes"):
        if k in data:
            data[k] = _as_text(data[k])
    return Pack.model_validate(data)


def load_pack(campaign_id: str, language: str, config_dir: Path | None = None) -> Pack | None:
    """The pack for this use case and language, whatever its status, or None when
    there is none or it cannot be read (a bad file never makes a language usable).
    Whether it may be used is decided by ``usable_for_voice`` / ``languages.available``."""
    from . import languages

    code = languages.normalize(language)
    if code is None or code != language:
        return None
    doc = _read(pack_path(campaign_id or DEFAULT_CAMPAIGN_ID, code, config_dir))
    if doc is None:
        return None
    try:
        pack = _model(doc)
    except ValidationError:
        log.warning(
            "language pack invalid",
            extra={"event": "language_pack.invalid", "campaign": campaign_id, "language": code},
        )
        return None
    if pack.language != code or pack.campaign != (campaign_id or DEFAULT_CAMPAIGN_ID):
        return None
    return pack


def pack_files(config_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """Every ``<campaign>.<lang>.yaml`` under i18n, as (campaign, language, path)."""
    from . import languages

    d = languages.pack_dir(config_dir)
    out = []
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.yaml")):
        stem = path.stem
        if "." not in stem:
            continue
        cid, lang = stem.split(".", 1)
        out.append((cid, lang, path))
    return out


def _campaign_for(campaign_id: str, config_dir: Path | None) -> Campaign | None:
    """The English spec a pack translates. A config_dir without the spec (tests
    that only write packs) falls back to the repo's spec."""
    for cdir in (config_dir, None) if config_dir is not None else (None,):
        try:
            return load_campaign(campaign_id, cdir)
        except (UnknownCampaign, OSError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------- lint


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


def _placeholder_problem(where: str, text: str, english: str) -> str | None:
    try:
        mine = placeholders(text)
    except ValueError as e:
        return f"{where}: bad braces ({e}); keep the English {{placeholders}} exactly"
    want = placeholders(english)
    if mine != want:
        show = lambda s: ", ".join(f"{{{n}}}" for n in sorted(s)) or "none"  # noqa: E731
        return f"{where}: placeholders {show(mine)} differ from the English ({show(want)})"
    return None


def emergency_problems(text: str, language: str) -> list[str]:
    """The emergency line must say 911 and keep "hang up now and call"."""
    primary = (language or "").split("-")[0].lower()
    words = EMERGENCY_WORDS.get(primary)
    if words is None:
        return [
            f"scripts.emergency: no emergency-wording check exists for {language}; "
            "a developer must add one to language_packs.EMERGENCY_WORDS before it can be approved"
        ]
    nine, hang, call = words
    folded = _fold(text)
    out = []
    if not _has_any(folded, nine):
        out.append('scripts.emergency: must say 911 ("911" or the spoken form)')
    if not _has_any(folded, hang) or not _has_any(folded, call):
        out.append("scripts.emergency: must keep the instruction to hang up now and call 911")
    return out


def pack_problems(
    doc: Mapping[str, Any] | Pack,
    campaign: Campaign | None = None,
    *,
    file_campaign: str | None = None,
    file_language: str | None = None,
) -> list[str]:
    """Every problem with a pack, one actionable line each. Empty = the pack is
    complete for voice and well-formed. ``doc`` is the raw YAML mapping (so extra
    keys are reported) or a loaded ``Pack``."""
    from . import languages

    raw: Mapping[str, Any] = doc.model_dump() if isinstance(doc, Pack) else doc
    out: list[str] = []
    for k in sorted(set(raw) - _TOP_KEYS):
        out.append(f"{k}: unknown key (a pack holds wording only; see docs/EDITING-QUESTIONS.md)")
    try:
        pack = (
            doc
            if isinstance(doc, Pack)
            else _model({k: v for k, v in raw.items() if k in _TOP_KEYS})
        )
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"])
            out.append(f"{where}: {err['msg']}")
        return out

    code = languages.normalize(pack.language)
    if code is None or code != pack.language:
        out.append(f"language: {pack.language!r} is not one of the 24 codes (e.g. es-US)")
    elif code in languages.ENGLISH:
        out.append("language: English uses the spec itself; no pack is needed")
    if file_language is not None and pack.language != file_language:
        out.append(f"language: {pack.language!r} does not match the file name ({file_language})")
    if file_campaign is not None and pack.campaign != file_campaign:
        out.append(f"campaign: {pack.campaign!r} does not match the file name ({file_campaign})")

    if pack.status == "approved":
        for k in ("reviewed_by", "approved_by", "approved_at"):
            if not getattr(pack, k).strip():
                out.append(f"{k}: required for status approved")
        if pack.approved_at.strip():
            try:
                date.fromisoformat(pack.approved_at.strip()[:10])
            except ValueError:
                out.append("approved_at: must be a date, YYYY-MM-DD")

    c = campaign or _campaign_for(pack.campaign, None)
    if c is None:
        out.append(f"campaign: no use case {pack.campaign!r} (config/campaigns/<id>.yaml)")
        return out

    # Voice scripts: exactly the spec's keys, same placeholders.
    for key in c.scripts:
        if not pack.scripts.get(key, "").strip():
            out.append(f"scripts.{key}: missing (the voice call says it)")
    for key in sorted(set(pack.scripts) - set(c.scripts)):
        out.append(f"scripts.{key}: not in the English spec (nothing says it)")
    for key, text in pack.scripts.items():
        if key in c.scripts and (p := _placeholder_problem(f"scripts.{key}", text, c.scripts[key])):
            out.append(p)
    if "emergency" in pack.scripts and pack.scripts["emergency"].strip():
        out += emergency_problems(pack.scripts["emergency"], pack.language)

    # Questions: every id asked, nothing the English does not have.
    for q in c.questions:
        pq = pack.questions.get(q.id)
        if pq is None or not pq.ask.strip():
            out.append(f"questions.{q.id}.ask: missing (the voice agent asks it)")
            continue
        if p := _placeholder_problem(f"questions.{q.id}.ask", pq.ask, q.ask):
            out.append(p)
        if pq.note and not q.note:
            out.append(f"questions.{q.id}.note: the English question has no note")
        for f in _SMS_QUESTION_KEYS:
            mine, english = getattr(pq, f), getattr(q, f)
            if mine and not english:
                out.append(f"questions.{q.id}.{f}: the English question has no {f}")
            elif mine and (p := _placeholder_problem(f"questions.{q.id}.{f}", mine, english)):
                out.append(p)
    for qid in sorted(set(pack.questions) - set(c.question_ids)):
        out.append(f"questions.{qid}: not a question of {c.id}")

    # SMS (optional in step 4): only the spec's lines, same placeholders.
    for key, text in pack.sms_scripts.items():
        if key not in c.sms_scripts:
            out.append(f"sms_scripts.{key}: not in the English spec")
        elif p := _placeholder_problem(f"sms_scripts.{key}", text, c.sms_scripts[key]):
            out.append(p)
    return out


def voice_complete(pack: Pack, campaign: Campaign) -> bool:
    """Every voice line and question wording present, and the lint clean."""
    return "voice" in campaign.channels and not pack_problems(pack, campaign)


def sms_complete(pack: Pack, campaign: Campaign) -> bool:
    """Every SMS line of the spec translated (and the voice lint clean). Not
    enough on its own for texts in step 4: see ``SMS_PACKS_ENABLED``."""
    if "sms" not in campaign.channels or pack_problems(pack, campaign):
        return False
    if any(not pack.sms_scripts.get(k, "").strip() for k in campaign.sms_scripts):
        return False
    for q in campaign.questions:
        pq = pack.questions.get(q.id)
        for f in ("sms", *_SMS_QUESTION_KEYS[1:]):
            if getattr(q, f) and not (pq and getattr(pq, f).strip()):
                return False
    return True


def channel_ready(pack: Pack, campaign: Campaign, channel: str) -> bool:
    if channel == "voice":
        return voice_complete(pack, campaign)
    if channel == "sms":
        return SMS_PACKS_ENABLED and sms_complete(pack, campaign)
    raise ValueError(f"unknown channel {channel!r}")


# --------------------------------------------------------------------------- use


def localize(campaign: Campaign, pack: Pack) -> Campaign:
    """The English spec with the pack's wording in place of the voice scripts and
    the questions' ``ask`` / ``note``. Everything else (ids, types, rules,
    triage, red flags) is the English spec's: a pack cannot change it."""
    scripts = {k: pack.scripts.get(k) or v for k, v in campaign.scripts.items()}
    questions = []
    for q in campaign.questions:
        pq = pack.questions.get(q.id)
        if pq is None:
            questions.append(q)
            continue
        questions.append(q.model_copy(update={"ask": pq.ask or q.ask, "note": pq.note or q.note}))
    return campaign.model_copy(update={"scripts": scripts, "questions": questions})


def drafts_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """``VOA_ALLOW_DRAFT_PACKS=1`` with ``VOA_ENV`` dev or test (set explicitly).
    Refused, loudly, anywhere else: a draft pack must never reach a patient."""
    env = os.environ if environ is None else environ
    if env.get(ALLOW_DRAFTS_ENV) != "1":
        return False
    if env.get("VOA_ENV") in DRAFT_ENVS:
        return True
    log.error(
        "draft language packs refused outside dev/test",
        extra={"event": "language_pack.drafts_refused", "env": env.get("VOA_ENV") or "unset"},
    )
    return False


def voice_pack_for_call(
    campaign_id: str | None,
    language: Any,
    call_in_english: Any = False,
    *,
    config_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Pack | None:
    """THE GATE for the spoken language of a call. The pack to speak, or None
    for English.

    A non-English patient is spoken to in their language only when that
    language's pack for the call's use case is approved (or a draft, in dev/test
    evals with ``VOA_ALLOW_DRAFT_PACKS=1``), complete for voice, and the admin has
    not turned on ``call_in_english``. Otherwise the call is in English, and it
    is only placed at all when the hold allowed it (``languages.hold_reason``)."""
    from . import languages
    from .compliance import coerce_bool

    if coerce_bool(call_in_english):
        return None
    code = languages.normalize(language) if language else None
    if code is None or code in languages.ENGLISH:
        return None
    cid = campaign_id or DEFAULT_CAMPAIGN_ID
    pack = load_pack(cid, code, config_dir)
    if pack is None:
        return None
    if not (pack.approved or (pack.status in ("draft", "in_review") and drafts_allowed(environ))):
        return None
    campaign = _campaign_for(cid, config_dir)
    if campaign is None or not voice_complete(pack, campaign):
        return None
    return pack


def localized_campaign(campaign: Campaign, language: str | None, config_dir: Path | None = None):
    """The spec in ``language`` for a call the gateway already put in that
    language (``state["call_language"]``); the English spec when there is none."""
    if not language:
        return campaign
    pack = load_pack(campaign.id, language, config_dir)
    return localize(campaign, pack) if pack is not None else campaign


def summary(config_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every pack on disk, for the admin console (read-only): status, who
    signed it, and which channels it is complete and usable for."""
    from . import languages

    rows = []
    for cid, lang, path in pack_files(config_dir):
        doc = _read(path)
        problems: list[str]
        campaign = None
        if doc is None:
            problems, pack = ["the file cannot be read as YAML"], None
        else:
            campaign = _campaign_for(cid, config_dir)
            problems = pack_problems(doc, campaign, file_campaign=cid, file_language=lang)
            try:
                pack = _model({k: v for k, v in doc.items() if k in _TOP_KEYS})
            except ValidationError:
                pack = None
        status = pack.status if pack else "invalid"
        voice = bool(pack) and not problems
        sms = bool(pack) and voice and campaign is not None and sms_complete(pack, campaign)
        rows.append(
            {
                "campaign_id": cid,
                "language": lang,
                "name": languages.name(lang),
                "status": status,
                "translated_by": pack.translated_by if pack else "",
                "reviewed_by": pack.reviewed_by if pack else "",
                "approved_by": pack.approved_by if pack else "",
                "approved_at": pack.approved_at if pack else "",
                "voice": {"complete": voice, "available": voice and status == "approved"},
                "sms": {
                    "complete": sms,
                    "available": sms and SMS_PACKS_ENABLED and status == "approved",
                },
                "problems": problems[:20],
                "needs": _needs(status, problems),
            }
        )
    return rows


def _needs(status: str, problems: list[str]) -> str:
    if problems:
        return "fix the wording problems"
    return {
        "draft": "needs translator review",
        "in_review": "needs approval",
        "approved": "",
        "retired": "retired",
    }.get(status, "fix the file")
