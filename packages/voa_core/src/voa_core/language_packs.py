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
    sms_copy: {...}           # the shared texts (invite, menu, DOB gate, STOP/HELP...):
                              # every key of voa_core.sms_copy.ENGLISH
    sms_scripts: {...}        # the questionnaire's own lines (every key of the spec's)
    # a question also carries sms / sms_if_* wording (where the English has it)

Lifecycle: a machine or a person drafts a pack, a qualified medical translator
reviews it (``reviewed_by``), and a named approver sets ``status: approved``
with ``approved_by`` and ``approved_at``. Only an approved pack whose wording is
complete and lint-clean makes a language *available* (``languages.available``),
per channel: voice needs the voice wording; texts also need the SMS wording
(``sms_copy``, ``sms_scripts``, question ``sms*``) complete and within the
encoding-aware segment limits (``sms_problems``).
For evals only, ``VOA_ALLOW_DRAFT_PACKS=1`` lets the gateway use a draft pack,
and only when ``VOA_ENV`` is ``dev`` or ``test`` (never in a deployed service).
The demo auto-approve (``languages.auto_approved``; dev/test/demo only) also uses
a draft pack's wording, for calls and texts, and machine translation where
there is no pack (docs/LANGUAGES-PLAN.md, "Language availability and approval").
The admin "Test by text" simulator may use a draft pack's SMS wording because it
sends nothing (``sms_pack``'s ``allow_drafts``); a real send refuses one
(``voa_api.sms_service.send_sms``).

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
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .campaign import DEFAULT_CAMPAIGN_ID, Campaign, UnknownCampaign, load_campaign
from .copy import placeholders

log = logging.getLogger(__name__)

PackStatus = Literal["draft", "in_review", "approved", "retired"]
CHANNELS = ("voice", "sms")

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
        "sms_copy",
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
    sms_copy: dict[str, str] = Field(default_factory=dict)
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


# --------------------------------------------------------------------------- SMS lint

#: Realistic values for the segment count (fictional): a long first name and org
#: name, a caller ID, the longest booked-time shape, a 60-character question echo.
SMS_SAMPLE = {
    "org_name": "Northgate Regional Health",
    "patient_name": "Maximiliano Fernandez",
    "voice_number": "(202) 555-0199",
    "support_email": "followup@northgate-health.example.org",
    "site": "la parte baja de la espalda",
    "question": "¿Cuándo puedo volver a manejar y subir escaleras sin ayuda...",
    "total": 9,
}
_SMS_TEXT_KEYS = ("sms", *_SMS_QUESTION_KEYS)


def _literal_problems(where: str, text: str, english: str) -> list[str]:
    """What carriers and emergency services need left untranslated."""
    out = []
    if re.search(r"(?<!\w)911(?!\w)", english) and not re.search(r"(?<!\w)911(?!\w)", text):
        out.append(f"{where}: must keep 911 in digits")
    if "tel:911" in english and "tel:911" not in text:
        out.append(f"{where}: must keep the tap-to-call link tel:911")
    if re.search(r"\bSTOP\b", english) and not re.search(r"\bSTOP\b", text):
        out.append(f"{where}: must keep the English keyword STOP (carriers require it)")
    if re.search(r"(?<!\w)C(?!\w)", english) and not re.search(r"(?<!\w)C(?!\w)", text):
        out.append(f'{where}: must keep the reply letter "C"')
    return out


def sms_messages(pack: Pack, campaign: Campaign) -> list[tuple[str, str]]:
    """Every text a conversation in this pack can send, rendered the way it is
    sent with the realistic values of ``SMS_SAMPLE`` (and a caller ID): (name,
    text). Needs a complete pack (``sms_problems`` without the segment check)."""
    from . import copy as messages
    from . import sms_copy
    from .copy import SiteCopy

    m = sms_copy.PackCopy(pack.sms_copy, pack.language, approved=pack.approved)
    c = localize_sms(campaign, pack)
    v = SMS_SAMPLE
    site = SiteCopy(
        org_name=v["org_name"],
        support_email=v["support_email"],
        voice_number=v["voice_number"],
        survey_enabled=True,
    )
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 12, 30, 9, 0, tzinfo=tz)
    when = m.humanize(datetime(2027, 9, 29, 10, 30, tzinfo=tz), now)  # "mié 29 sep 2027, ..."
    out = [
        ("invite", m.invite(v["patient_name"], site)),
        ("unclear_choice", m.unclear_choice(site)),
        ("gave_up_choice", m.gave_up_choice()),
        ("calling_now", m.calling_now(site)),
        ("cannot_call_back", m.cannot_call_back()),
        ("ask_for_time", m.ask_for_time()),
        ("unclear_time", m.unclear_time()),
        ("gave_up_time", m.gave_up_time()),
        ("booked", m.booked(when, True, site)),
        ("already_booked", m.already_booked(when)),
        ("booking_canceled", m.booking_canceled(site)),
        ("nothing_to_cancel", m.nothing_to_cancel()),
        ("emergency", m.emergency()),
        ("opted_out", m.opted_out(site)),
        ("help", m.help_message(site)),
        ("session_closed", m.session_closed(site)),
        ("ask_dob", m.ask_dob(v["patient_name"])),
        ("dob_unreadable", m.dob_unreadable()),
        ("dob_failed", m.dob_failed()),
        ("survey_stopped", m.survey_stopped()),
        ("confirm_time", m.confirm_time_prompt(when, False)),
        ("confirm_time_adjusted", m.confirm_time_prompt(when, True)),
    ]
    if "sms" not in c.channels or not c.sms_scripts:
        return out
    numbered = [q for q in c.questions if not q.when and q.sms]
    total = max(len(numbered), int(v["total"]))
    out.append(("sms_scripts.preamble", messages.survey_preamble(total, c)))
    longest = ""
    for i, q in enumerate(numbered):
        text = messages.question(i + 1, total, q.sms, c)
        out.append((f"questions.{q.id}.sms", text))
        longest = max(longest, q.sms, key=len)
    for q in c.questions:
        if q.when and q.sms:
            out.append((f"questions.{q.id}.sms", q.sms))
            longest = max(longest, q.sms, key=len)
        for f in _SMS_QUESTION_KEYS[1:]:
            if getattr(q, f):
                out.append((f"questions.{q.id}.{f}", getattr(q, f).format(site=v["site"])))
    scripts = c.sms_scripts
    out.append(("sms_scripts.unclear_answer", messages.unclear_answer(longest, c)))
    for key in ("ask_question", "another_question", "cut_short", "file_failed"):
        if key in scripts:
            out.append((f"sms_scripts.{key}", scripts[key]))
    if "question_noted" in scripts:
        out.append(("sms_scripts.question_noted", messages.question_noted(v["question"], c)))
    if "question_noted_last" in scripts:
        out.append(
            ("sms_scripts.question_noted_last", messages.question_noted_last(v["question"], c))
        )
    for key, fn in (
        ("closing_routine", messages.survey_done_routine),
        ("closing_urgent", messages.survey_done_urgent),
    ):
        if key in scripts:
            out.append((f"sms_scripts.{key}", fn(True, c)))
    return out


def sms_segment_report(pack: Pack, campaign: Campaign) -> list[dict[str, Any]]:
    """Encoding and segments of every text (``sms_messages``), for the lint, the
    console and docs/HANDOFF.md. Empty when the SMS wording is incomplete."""
    from . import sms_copy

    if _sms_shape_problems(pack, campaign):
        return []
    rows = []
    for name, text in sms_messages(pack, campaign):
        seg = sms_copy.segments(text)
        rows.append(
            {
                "name": name,
                "encoding": seg.encoding,
                "characters": seg.units,
                "segments": seg.segments,
            }
        )
    return rows


def _sms_shape_problems(pack: Pack, campaign: Campaign) -> list[str]:
    from . import sms_copy

    out: list[str] = []
    english = sms_copy.ENGLISH
    for key in sorted(set(pack.sms_copy) - set(english)):
        out.append(f"sms_copy.{key}: not a shared SMS text (see voa_core/sms_copy.py)")
    for key, en_text in english.items():
        text = pack.sms_copy.get(key, "")
        if not str(text).strip():
            out.append(f"sms_copy.{key}: missing")
            continue
        if p := _placeholder_problem(f"sms_copy.{key}", text, en_text):
            out.append(p)
        out += _literal_problems(f"sms_copy.{key}", text, en_text)
    for key, digit in (("menu_call_now", "1"), ("menu_pick_time", "2"), ("menu_by_text", "3")):
        text = pack.sms_copy.get(key, "")
        if text and not re.match(rf"\s*{digit}\b", text):
            out.append(f'sms_copy.{key}: must start with the digit "{digit}" (replies are digits)')
    for key, n in (("weekdays", 7), ("months", 12)):
        text = pack.sms_copy.get(key, "")
        if text and len(text.split()) != n:
            out.append(f"sms_copy.{key}: must be {n} space-separated words")
    for key in ("ask_dob", "dob_unreadable"):
        text = pack.sms_copy.get(key, "")
        if text and "DD/MM/" not in text:
            out.append(
                f"sms_copy.{key}: must ask for the date day first (DD/MM/...): "
                "a non-English patient's date is read day first"
            )

    if "sms" not in campaign.channels:
        return out
    for key, en_text in campaign.sms_scripts.items():
        text = pack.sms_scripts.get(key, "")
        if not text.strip():
            out.append(f"sms_scripts.{key}: missing")
            continue
        out += _literal_problems(f"sms_scripts.{key}", text, en_text)
    for q in campaign.questions:
        pq = pack.questions.get(q.id)
        for f in _SMS_TEXT_KEYS:
            en_text = getattr(q, f)
            if not en_text:
                continue
            text = getattr(pq, f) if pq is not None else ""
            if not text.strip():
                out.append(f"questions.{q.id}.{f}: missing (the text questionnaire sends it)")
                continue
            out += _literal_problems(f"questions.{q.id}.{f}", text, en_text)
    return out


def sms_problems(pack: Pack, campaign: Campaign) -> list[str]:
    """Every problem with the pack's SMS wording, one line each. Empty = texts
    can be sent in this language once the pack is approved (with the voice lint
    clean too: ``sms_complete``). Checks: every shared text (``sms_copy``), every
    questionnaire line and question text present; the English placeholders; 911,
    tel:911, STOP and the reply letter C kept; the menu digits; a day-first DOB
    prompt; and no text above ``FAIL_ABOVE_SEGMENTS`` segments in its encoding."""
    from . import sms_copy

    out = _sms_shape_problems(pack, campaign)
    if out:
        return out
    for row in sms_segment_report(pack, campaign):
        if row["segments"] > sms_copy.FAIL_ABOVE_SEGMENTS:
            out.append(
                f"{row['name']}: {row['segments']} segments ({row['encoding']}, "
                f"{row['characters']} characters with long sample values); "
                f"the most is {sms_copy.FAIL_ABOVE_SEGMENTS}: shorten it"
            )
    return out


def sms_warnings(pack: Pack, campaign: Campaign) -> list[str]:
    """Texts above ``WARN_ABOVE_SEGMENTS`` segments: allowed, but each segment
    is billed and long texts arrive less reliably."""
    from . import sms_copy

    return [
        f"{row['name']}: {row['segments']} segments ({row['encoding']})"
        for row in sms_segment_report(pack, campaign)
        if sms_copy.WARN_ABOVE_SEGMENTS < row["segments"] <= sms_copy.FAIL_ABOVE_SEGMENTS
    ]


def sms_complete(pack: Pack, campaign: Campaign) -> bool:
    """Every SMS text translated and lint-clean (``sms_problems``), and the voice
    lint clean. With ``status: approved`` this makes texts available."""
    if "sms" not in campaign.channels or pack_problems(pack, campaign):
        return False
    return not sms_problems(pack, campaign)


def channel_ready(pack: Pack, campaign: Campaign, channel: str) -> bool:
    if channel == "voice":
        return voice_complete(pack, campaign)
    if channel == "sms":
        return sms_complete(pack, campaign)
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


def localize_sms(campaign: Campaign, pack: Pack) -> Campaign:
    """The English spec with the pack's SMS wording (``sms_scripts`` and the
    questions' ``sms`` / ``sms_if_*``) in place. Wording only, like ``localize``."""
    sms_scripts = {k: pack.sms_scripts.get(k) or v for k, v in campaign.sms_scripts.items()}
    questions = []
    for q in campaign.questions:
        pq = pack.questions.get(q.id)
        if pq is None:
            questions.append(q)
            continue
        questions.append(
            q.model_copy(update={f: getattr(pq, f) or getattr(q, f) for f in _SMS_TEXT_KEYS})
        )
    return campaign.model_copy(update={"sms_scripts": sms_scripts, "questions": questions})


_SMS_READY: dict[tuple[str, int], bool] = {}


def sms_pack(
    campaign_id: str | None,
    language: Any,
    *,
    allow_drafts: bool = False,
    config_dir: Path | None = None,
) -> Pack | None:
    """The pack whose SMS wording a conversation in ``language`` uses, or None.

    Approved and SMS-complete (``sms_complete``); a draft or in-review pack only
    with ``allow_drafts``, which only the dry-run simulator passes (it sends
    nothing). English, an unknown language or a missing pack: None. The readiness
    check is cached per file version (the lint renders every text)."""
    from . import languages

    code = languages.normalize(language) if language else None
    if code is None or code in languages.ENGLISH:
        return None
    cid = campaign_id or DEFAULT_CAMPAIGN_ID
    pack = load_pack(cid, code, config_dir)
    if pack is None:
        return None
    # The demo auto-approve (languages.auto_approved) uses a draft's wording too.
    drafts_ok = allow_drafts or languages.auto_approved()
    if not (pack.approved or (drafts_ok and pack.status in ("draft", "in_review"))):
        return None
    path = pack_path(cid, code, config_dir)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _SMS_READY:
        campaign = _campaign_for(cid, config_dir)
        _SMS_READY[key] = campaign is not None and sms_complete(pack, campaign)
    return pack if _SMS_READY[key] else None


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
    evals with ``VOA_ALLOW_DRAFT_PACKS=1`` or with the demo auto-approve on),
    complete for voice, and the admin has
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
    if not (
        pack.approved
        or (
            pack.status in ("draft", "in_review")
            and (languages.auto_approved() or drafts_allowed(environ))
        )
    ):
        return None
    campaign = _campaign_for(cid, config_dir)
    if campaign is None or not voice_complete(pack, campaign):
        return None
    return pack


def localized_campaign(
    campaign: Campaign,
    language: str | None,
    config_dir: Path | None = None,
    source: str | None = None,
):
    """The spec in ``language`` for a call the gateway already put in that
    language (``state["call_language"]``); the English spec when there is none.
    ``source`` "machine" (demo auto-approve, no usable pack): the English spec,
    even if a retired pack file exists (the model translates it live)."""
    if not language or source == "machine":
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
        sms_issues = sms_problems(pack, campaign) if pack and campaign else []
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
                    "available": sms and status == "approved",
                    "problems": sms_issues[:20],
                    "warnings": sms_warnings(pack, campaign) if sms else [],
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
