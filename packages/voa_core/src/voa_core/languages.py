"""Patient languages (docs/LANGUAGES-PLAN.md, step 1).

A patient's preferred language is one of the 24 languages the live voice model
speaks. A language is *available* for a use case only when the call can be made
in it today: English (both English codes use the English wording), plus any
language with an approved, complete language pack (``voa_core.language_packs``;
step 4 ships a draft Spanish pack, which does not count). A patient whose
language is not available is **held**: never auto-dialled and never called in
English silently, unless an admin turns on ``call_in_english`` for them.

Pure functions plus one file read (the language-pack seam, ``available``).
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
    today. In step 4 an approved voice pack makes a language available for voice
    only; texts stay English-only until step 5."""
    from .campaign import DEFAULT_CAMPAIGN_ID

    return set(ENGLISH) | approved_packs(campaign_id or DEFAULT_CAMPAIGN_ID, config_dir, channel)


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
    """``GET /api/languages`` rows for one use case."""
    ok = available(campaign_id, config_dir)
    return [
        {"code": lang.code, "name": lang.name, "native": lang.native, "available": lang.code in ok}
        for lang in SUPPORTED
    ]
