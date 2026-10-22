"""Patient languages (docs/LANGUAGES-PLAN.md, step 1).

A patient's preferred language is one of the 24 languages the live voice model
speaks. A language is *available* for a use case only when the call can be made
in it today: English (both English codes use the English wording), plus any
language with an approved, complete language pack (``voa_core.language_packs``;
the repo ships a draft Spanish pack, which does not count). A patient whose
language is not available is **held**: never auto-dialled and never called in
English silently, unless an admin turns on ``call_in_english`` for them.

Pure functions plus one file read (the language-pack seam, ``available``).

**Demo auto-approve** (``languages.auto_approve`` in the config; docs/LANGUAGES-
PLAN.md, "Language availability and approval"). When on, every language is
available for calls and texts and nobody is held. Each language then reports its
wording source (``wording_source``): ``approved`` (an approved pack), ``draft_pack``
(a draft or in-review pack complete for the channel, e.g. es-US) or ``machine``
(no pack: live machine translation of the English wording, unreviewed). The
config refuses the switch outside dev/test/demo, and ``configure`` refuses it
again. Off (the default and production): everything here is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT = "en-US"


@dataclass(frozen=True)
class Language:
    code: str
    name: str  # English name
    native: str


#: The live model's languages, in the order the patient form lists them.
SUPPORTED: tuple[Language, ...] = (
    Language("ar-EG", "Arabic (Egyptian)", "العربية (مصر)"),
    Language("bn-BD", "Bengali (Bangladesh)", "বাংলা"),
    Language("nl-NL", "Dutch", "Nederlands"),
    Language("en-IN", "English (India)", "English (India)"),
    Language("en-US", "English (US)", "English (US)"),
    Language("fr-FR", "French (France)", "Français"),
    Language("de-DE", "German", "Deutsch"),
    Language("hi-IN", "Hindi", "हिन्दी"),
    Language("id-ID", "Indonesian", "Bahasa Indonesia"),
    Language("it-IT", "Italian", "Italiano"),
    Language("ja-JP", "Japanese", "日本語"),
    Language("ko-KR", "Korean", "한국어"),
    Language("mr-IN", "Marathi", "मराठी"),
    Language("pl-PL", "Polish", "Polski"),
    Language("pt-BR", "Portuguese (Brazil)", "Português (Brasil)"),
    Language("ro-RO", "Romanian", "Română"),
    Language("ru-RU", "Russian", "Русский"),
    Language("es-US", "Spanish (US)", "Español"),
    Language("ta-IN", "Tamil", "தமிழ்"),
    Language("te-IN", "Telugu", "తెలుగు"),
    Language("th-TH", "Thai", "ไทย"),
    Language("tr-TR", "Turkish", "Türkçe"),
    Language("uk-UA", "Ukrainian", "Українська"),
    Language("vi-VN", "Vietnamese", "Tiếng Việt"),
)
BY_CODE: dict[str, Language] = {lang.code: lang for lang in SUPPORTED}
CODES: tuple[str, ...] = tuple(BY_CODE)

#: Callable today with the English wording, whatever packs exist.
ENGLISH: frozenset[str] = frozenset({"en-US", "en-IN"})

#: A bare primary tag or base name shared by two codes resolves to this one.
_PREFERRED = {"en": "en-US", "english": "en-US"}


def _base(name: str) -> str:
    return name.split("(", 1)[0].strip().casefold()


def _aliases() -> dict[str, str]:
    """Lower-cased spellings -> code: the code, a bare primary tag that maps to
    exactly one code, the English name, its base ("spanish") when unique, and
    the native name."""
    out: dict[str, str] = {}
    ambiguous: set[str] = set()

    def add(key: str, code: str) -> None:
        key = key.casefold()
        if key in out and out[key] != code:
            ambiguous.add(key)
        out.setdefault(key, code)

    for lang in SUPPORTED:
        out[lang.code.casefold()] = lang.code
        add(lang.code.split("-")[0], lang.code)
        add(lang.name, lang.code)
        add(_base(lang.name), lang.code)
        add(lang.native, lang.code)
    for key in ambiguous:
        del out[key]
    out.update(_PREFERRED)
    return out


_ALIASES = _aliases()


def normalize(value: Any) -> str | None:
    """A supported code for ``value``, or None when it is not one of the 24.

    Case-insensitive; ``es_US`` is read as ``es-US``; a bare tag (``es``, ``pt``)
    or an English name (``Spanish``, ``spanish (us)``) is accepted when it names
    exactly one supported code (``en`` and ``English`` mean en-US)."""
    if not isinstance(value, str):
        return None
    key = " ".join(value.strip().replace("_", "-").split()).casefold()
    return _ALIASES.get(key) if key else None


def name(code: str | None) -> str:
    """The English name, e.g. "Spanish (US)"; the raw value if unsupported."""
    lang = BY_CODE.get(normalize(code) or "")
    return lang.name if lang else str(code or DEFAULT)


def display(code: str | None) -> str:
    """Nurse-facing label: "Español (Spanish US)"; "English (US)" for English."""
    lang = BY_CODE.get(normalize(code) or "")
    if lang is None:
        return str(code or DEFAULT)
    if lang.native == lang.name:
        return lang.name
    return f"{lang.native} ({lang.name.replace('(', '').replace(')', '')})"


# --------------------------------------------------------------------------- auto-approve

#: Where the wording of a non-English call or text comes from.
APPROVED, DRAFT_PACK, MACHINE = "approved", "draft_pack", "machine"
SOURCES: tuple[str, ...] = (APPROVED, DRAFT_PACK, MACHINE)
#: Wording no qualified translator has reviewed.
UNREVIEWED: frozenset[str] = frozenset({DRAFT_PACK, MACHINE})

_AUTO_APPROVE = False


class AutoApproveRefused(RuntimeError):
    """The demo-only switch was turned on outside dev/test/demo."""


def configure(cfg: Any) -> None:
    """Apply ``cfg.languages.auto_approve`` to this process (called by
    ``config.load_config`` and each service's startup). Refuses the switch
    outside dev/test/demo (the config validator already does; this is the
    second lock, for a config built some other way)."""
    from .config import AUTO_APPROVE_ENVS

    global _AUTO_APPROVE
    on = bool(getattr(getattr(cfg, "languages", None), "auto_approve", False))
    env = getattr(cfg, "env", None)
    if on and env not in AUTO_APPROVE_ENVS:
        _AUTO_APPROVE = False
        raise AutoApproveRefused(f"languages.auto_approve is demo-only; refused in VOA_ENV={env!r}")
    if on and not _AUTO_APPROVE:
        import logging

        logging.getLogger(__name__).warning(
            "DEMO: all languages auto-approved (unreviewed wording)",
            extra={"event": "languages.auto_approve_on", "env": env},
        )
    _AUTO_APPROVE = on


def auto_approved() -> bool:
    """True when the demo switch is on in this process."""
    return _AUTO_APPROVE


# --------------------------------------------------------------------------- packs


def pack_dir(config_dir: Path | None = None) -> Path:
    from .campaign import default_config_dir

    return (config_dir or default_config_dir()) / "campaigns" / "i18n"


def approved_packs(
    campaign_id: str, config_dir: Path | None = None, channel: str = "voice"
) -> set[str]:
    """THE SEAM for language packs (plan §3.2): the languages with an approved
    pack for this use case whose wording is complete for ``channel``, read from
    ``config/campaigns/i18n/<id>.<lang>.yaml`` (``voa_core.language_packs``). A
    missing, unreadable, unapproved, incomplete or lint-failing pack does not
    count (fail closed: the patient stays held). Draft packs never count here,
    whatever ``VOA_ALLOW_DRAFT_PACKS`` says: that switch is for evals only and
    acts on the gateway, never on who may be dialled."""
    from . import language_packs as lp
    from .campaign import DEFAULT_CAMPAIGN_ID

    cid = campaign_id or DEFAULT_CAMPAIGN_ID
    out: set[str] = set()
    d = pack_dir(config_dir)
    if not d.is_dir():
        return out
    for path in d.glob(f"{cid}.*.yaml"):
        code = normalize(path.stem[len(cid) + 1 :])
        if code is None or code != path.stem[len(cid) + 1 :] or code in ENGLISH:
            continue
        pack = lp.load_pack(cid, code, config_dir)
        if pack is None or not pack.approved:
            continue
        campaign = lp._campaign_for(cid, config_dir)
        if campaign is not None and lp.channel_ready(pack, campaign, channel):
            out.add(code)
    return out


def available(
    campaign_id: str | None, config_dir: Path | None = None, channel: str = "voice"
) -> set[str]:
    """The languages a use case can be called (``voice``) or texted (``sms``) in
    today: English, plus every language whose pack is approved and complete for
    that channel. For texts that means the SMS wording too (``sms_copy``,
    ``sms_scripts``, question ``sms*``), lint-clean within the segment limits
    (``language_packs.sms_problems``, step 5). Drafts never count.

    With the demo auto-approve on: all 24, on both channels (``wording_source``
    says where each one's wording comes from)."""
    from .campaign import DEFAULT_CAMPAIGN_ID

    if _AUTO_APPROVE:
        return set(CODES)
    return set(ENGLISH) | approved_packs(campaign_id or DEFAULT_CAMPAIGN_ID, config_dir, channel)


def wording_source(
    language: Any,
    campaign_id: str | None,
    channel: str = "voice",
    config_dir: Path | None = None,
) -> str | None:
    """Where a call (``voice``) or text (``sms``) in ``language`` gets its wording:
    ``approved``, ``draft_pack`` or ``machine`` (auto-approve only), or None when
    the language is English (the spec itself) or not available (held).

    ``draft_pack``: a draft or in-review pack whose wording is complete for the
    channel. A retired pack does not count (its wording was withdrawn)."""
    from .campaign import DEFAULT_CAMPAIGN_ID

    code = normalize(language) if language else None
    if code is None or code in ENGLISH:
        return None
    cid = campaign_id or DEFAULT_CAMPAIGN_ID
    return _source(code, cid, channel, approved_packs(cid, config_dir, channel), config_dir)


def _source(
    code: str, cid: str, channel: str, approved: set[str], config_dir: Path | None
) -> str | None:
    from . import language_packs as lp

    if code in approved:
        return APPROVED
    if not _AUTO_APPROVE:
        return None
    pack = lp.load_pack(cid, code, config_dir)
    if pack is not None and pack.status in ("draft", "in_review"):
        campaign = lp._campaign_for(cid, config_dir)
        if campaign is not None and lp.channel_ready(pack, campaign, channel):
            return DRAFT_PACK
    return MACHINE


def call_wording_source(
    language: Any,
    campaign_id: str | None,
    call_in_english: Any = False,
    config_dir: Path | None = None,
) -> str | None:
    """``wording_source`` for a call: None when the call is spoken in English
    (an English patient, or an admin chose "Call in English")."""
    from .compliance import coerce_bool

    if coerce_bool(call_in_english):
        return None
    return wording_source(language, campaign_id, "voice", config_dir)


def text_block(
    language: Any,
    campaign_id: str | None,
    call_in_english: Any = False,
    config_dir: Path | None = None,
) -> str | None:
    """Why this patient must not get a text now, or None when they may.

    A non-English patient (not switched to English by an admin) is never texted
    in English: without approved text wording in their language the text is
    skipped, with this reason stamped on the call ("no approved Spanish (US)
    text wording")."""
    from .compliance import coerce_bool

    if coerce_bool(call_in_english) or language is None or language == "":
        return None
    code = normalize(language)
    if code is None:
        return f"no approved text wording for language {language!r}"
    if code in available(campaign_id, config_dir, channel="sms"):
        return None
    return f"no approved {name(code)} text wording"


def text_language(
    language: Any,
    campaign_id: str | None,
    call_in_english: Any = False,
    config_dir: Path | None = None,
) -> str | None:
    """The language a new text conversation is written in: the patient's, when
    it is not English, the admin has not chosen "Call in English", and texts are
    available in it (an approved, complete SMS pack). None means English, which
    is only right when ``text_block`` is None too."""
    from .compliance import coerce_bool

    if coerce_bool(call_in_english) or not language:
        return None
    code = normalize(language)
    if code is None or code in ENGLISH:
        return None
    return code if code in available(campaign_id, config_dir, channel="sms") else None


# --------------------------------------------------------------------------- the hold


def hold_reason(
    language: Any,
    campaign_id: str | None,
    call_in_english: bool = False,
    config_dir: Path | None = None,
) -> str | None:
    """Why this patient may not be called now, or None when they may.

    None for an available language, or when an admin chose ``call_in_english``.
    A blank language is English (the default). An unsupported value is held too."""
    if call_in_english is True:
        return None
    if language is None or language == "":
        return None
    code = normalize(language)
    if code is None:
        return f"Language {language!r} is not supported"
    if code in ENGLISH or code in available(campaign_id, config_dir):
        return None
    return f"Waiting for {name(code)} support"


def entry_hold_reason(
    entry: Mapping[str, Any], run_campaign_id: str | None = None, config_dir: Path | None = None
) -> str | None:
    """``hold_reason`` for a call-list entry: its own use case, else the run's."""
    from .compliance import coerce_bool

    return hold_reason(
        entry.get("language"),
        entry.get("campaign_id") or run_campaign_id,
        coerce_bool(entry.get("call_in_english")),
        config_dir,
    )


def held_message(patient_name: str, language: Any) -> str:
    """The nurse-facing refusal for "call now" on a held patient (a 409)."""
    who = (patient_name or "This patient").strip()
    lang = name(language)
    return (
        f"{who}'s language is {lang}. Calls in {lang} aren't available yet. "
        "Turn on 'Call in English' to call in English."
    )


def unsupported_message(value: Any) -> str:
    return f"language {value!r} is not supported; use one of: {', '.join(CODES)}"


def catalog(campaign_id: str | None, config_dir: Path | None = None) -> list[dict[str, Any]]:
    """``GET /api/languages`` rows for one use case. With the demo auto-approve
    on, each row also says where its wording comes from (``source`` for calls,
    ``sms_source`` for texts: approved | draft_pack | machine); off, the rows
    are exactly as before."""
    ok = available(campaign_id, config_dir)
    src = sources(campaign_id, config_dir) if _AUTO_APPROVE else None
    return [
        {
            "code": lang.code,
            "name": lang.name,
            "native": lang.native,
            "available": lang.code in ok,
            **(
                {"source": src[lang.code]["voice"], "sms_source": src[lang.code]["sms"]}
                if src is not None
                else {}
            ),
        }
        for lang in SUPPORTED
    ]


def sources(campaign_id: str | None, config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """{code: {voice, sms}} wording sources for one use case (``GET /api/languages``):
    English is approved (the spec itself); None means not available (held)."""
    from .campaign import DEFAULT_CAMPAIGN_ID

    cid = campaign_id or DEFAULT_CAMPAIGN_ID
    approved = {ch: approved_packs(cid, config_dir, ch) for ch in ("voice", "sms")}
    out: dict[str, dict[str, Any]] = {}
    for lang in SUPPORTED:
        if lang.code in ENGLISH:
            out[lang.code] = {"voice": APPROVED, "sms": APPROVED}
            continue
        out[lang.code] = {
            ch: _source(lang.code, cid, ch, approved[ch], config_dir) for ch in ("voice", "sms")
        }
    return out
