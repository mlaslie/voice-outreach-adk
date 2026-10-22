"""Translation to English (docs/LANGUAGES-PLAN.md §3.6, step 2).

One small interface, used by the post-call transcript task and the by-text
report filing today, and by inbound SMS normalisation in step 3:

    await translator.translate_turns(["¿Tengo fiebre?", "Yes"], target="en")
    -> [TurnTranslation(lang="es", text_en="Do I have a fever?"),
        TurnTranslation(lang="en", text_en=None)]

``lang`` is the detected language of each turn (BCP-47-ish: "es", "es-US",
"vi"); ``text_en`` is None when the turn is already English. A translator
**raises** on failure (``TranslationFailed``); it never returns a made-up or
partial result. The Gemini implementation lives in
``voa_agents.translate`` (where google-genai lives); :class:`FakeTranslator`
is for tests.

``looks_english`` is a cheap HINT, never a gate (step 3, 22 Oct 2026). Since
the safety layer, every transcript with at least one patient turn is
translated whatever it looks like: unaccented Spanish ("dolor de pecho") can
look English, and skipping it would bypass the post-call re-check. It is used
only where a false "not English" is the safe direction: a typed voice answer
that does not look English is refused ("record it in English"), a patient
question that does not look English is classified immediate, a knowledge-base
question that does not look English is deferred, and an inbound text that does
not look English is translated. Nothing may treat "looks English" as "was
checked".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from . import languages

TASK_PATH = "/tasks/transcript-translate"


@dataclass(frozen=True)
class TurnTranslation:
    lang: str
    text_en: str | None  # None: the turn is already English (or has no words)

    def to_dict(self) -> dict[str, Any]:
        return {"lang": self.lang, "text_en": self.text_en}


class TranslationFailed(Exception):
    """The translator could not produce a faithful translation for every turn."""


class Translator(Protocol):
    model: str

    async def translate_turns(
        self, turns: list[str], *, target: str = "en"
    ) -> list[TurnTranslation]: ...


# --------------------------------------------------------------------------- language tags


def is_english(lang: str | None) -> bool:
    return (lang or "").strip().lower().replace("_", "-").split("-")[0] == "en"


def to_supported(lang: str | None) -> str:
    """A detected tag ("es", "es-MX", "zh") mapped to one of the 24 codes when it
    names one, else the tag as given, lower-cased ("zh"). English -> en-US."""
    raw = (lang or "").strip().replace("_", "-")
    if not raw or is_english(raw):
        return languages.DEFAULT
    code = languages.normalize(raw) or languages.normalize(raw.split("-")[0])
    return code or raw.lower()


def majority_language(langs: Sequence[str | None]) -> str:
    """The language most of these turns were in (supported codes), ties to the
    first seen; en-US when there are none."""
    counts: dict[str, int] = {}
    for lang in langs:
        if lang:
            code = to_supported(lang)
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        return languages.DEFAULT
    return max(counts, key=lambda c: counts[c])  # dicts keep first-seen order on ties


# --------------------------------------------------------------------------- the heuristic

# Common words of the Latin-script languages on the list that are NOT English
# words (so "no", "me", "a", "die", "son" are left out). A turn using any of
# them is sent to the model.
_FOREIGN = frozenset(
    """
    que qué el la los las una por para pero porque como cómo muy tengo tiene estoy está
    sí bien gracias dolor fiebre usted ustedes hola yo señor señora puedo cuando
    também não sim obrigado obrigada você eu estou muito tenho mas uma dor febre
    oui merci je suis vous nous avec mais très bonjour douleur fièvre j'ai c'est
    ich nicht danke bitte und ist das mir sehr habe schmerzen fieber guten
    grazie sono buongiorno molto ho dolore febbre perché
    ik niet heb het een pijn koorts goedendag
    tak dziękuję jestem nie ból gorączka dzień dobry
    mulțumesc sunt durere febră bună
    evet hayır teşekkür ederim ağrı ateş merhaba
    saya tidak terima kasih sakit demam selamat
    không cảm ơn tôi đau sốt vâng chào
    si talvez sei biet
    quiero hablar alguien mucho mucha muchos poco ahora hoy ayer tambien también
    pierna brazo pecho cabeza herida peor mejor hinchada hinchado hinchazon duele
    sangre sangrado respirar medicina pastillas enfermera llamar llamame
    quero falar perna peito jambe poitrine parler veux
    cero uno dos tres cuatro cinco seis siete ocho nueve diez
    enero febrero marzo mayo junio julio agosto septiembre setiembre octubre noviembre diciembre
    janeiro fevereiro março maio junho julho setembro outubro novembro dezembro
    """.split()
)
# Multi-word "I don't know" / "maybe" phrases whose words are each English-looking
# ("no se" is "no" + "se"): "no" is English, so these must be phrases.
_FOREIGN_PHRASES = re.compile(
    r"\b(?:no se|tal vez|nao sei|je ne sais|weiss nicht|non lo so|khong biet|nie wiem|"
    r"bilmiyorum|tidak tahu|weet het niet|nu stiu)\b"
)
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def looks_english(text: str) -> bool:
    """True when ``text`` has no letters outside plain ASCII, no common word of
    another Latin-script language on the list and no foreign "don't know" phrase.
    Short turns ("OK", "7", "no") count as English. A HINT, not a safety gate
    (see the module docstring): callers use it only where "not English" is the
    safe reading."""
    for ch in text:
        if unicodedata.category(ch).startswith("L") and not ("a" <= ch.lower() <= "z"):
            return False
    words = {w.lower() for w in _WORD.findall(text)}
    if words & _FOREIGN:
        return False
    return not _FOREIGN_PHRASES.search(" ".join(_WORD.findall(text.lower())))


def all_english(texts: Sequence[str]) -> bool:
    return all(looks_english(t) for t in texts)


# --------------------------------------------------------------------------- applying


def apply_to_turns(
    turns: Sequence[Mapping[str, Any]], results: Sequence[TurnTranslation]
) -> list[dict[str, Any]]:
    """The transcript turns with ``lang`` and ``text_en`` added. The original
    ``text`` is never changed."""
    if len(results) != len(turns):
        raise TranslationFailed(f"expected {len(turns)} translations, got {len(results)}")
    out = []
    for turn, r in zip(turns, results, strict=True):
        out.append({**turn, "lang": to_supported(r.lang), "text_en": r.text_en})
    return out


def spoken_languages(turns: Sequence[Mapping[str, Any]], role: str = "patient") -> list[str]:
    """The distinct languages of ``role``'s turns, first seen first."""
    seen: dict[str, None] = {}
    for t in turns:
        if t.get("role") == role and t.get("lang"):
            seen.setdefault(to_supported(t["lang"]), None)
    return list(seen)


# --------------------------------------------------------------------------- the fake


class FakeTranslator:
    """For tests. ``table`` maps an original text to (lang, English); a text not
    in it is English (``text_en`` None). ``fail`` raises TranslationFailed.

    Outbound (``translate_out``, English -> a patient's language): ``out`` maps
    (text, target) to the translation, or is a function of (text, target); by
    default the text comes back as ``"[<target>] <text>"`` (STOP, 911 and C
    survive). ``fail_out`` raises TranslationFailed."""

    model = "fake-translator"

    def __init__(
        self,
        table: Mapping[str, tuple[str, str]] | None = None,
        *,
        fail: bool | str = False,
        detect: Callable[[str], tuple[str, str | None]] | None = None,
        out: Mapping[tuple[str, str], str] | Callable[[str, str], str] | None = None,
        fail_out: bool | str = False,
    ):
        self.table = dict(table or {})
        self.fail = fail
        self.detect = detect
        self.out = out
        self.fail_out = fail_out
        self.calls: list[list[str]] = []
        self.out_calls: list[tuple[list[str], str]] = []

    async def translate_out(self, texts: list[str], *, target: str) -> list[str]:
        self.out_calls.append((list(texts), target))
        if self.fail_out:
            raise TranslationFailed(
                self.fail_out if isinstance(self.fail_out, str) else "fake failure"
            )
        result = []
        for t in texts:
            if callable(self.out):
                result.append(self.out(t, target))
            elif self.out is not None and (t, target) in self.out:
                result.append(self.out[(t, target)])
            else:
                result.append(f"[{target}] {t}")
        return result

    async def translate_turns(self, turns: list[str], *, target: str = "en"):
        self.calls.append(list(turns))
        if self.fail:
            raise TranslationFailed(self.fail if isinstance(self.fail, str) else "fake failure")
        out = []
        for t in turns:
            if t in self.table:
                lang, en = self.table[t]
                out.append(TurnTranslation(lang, en))
            elif self.detect:
                out.append(TurnTranslation(*self.detect(t)))
            else:
                out.append(TurnTranslation("en", None))
        return out


# --------------------------------------------------------------------------- the task


async def enqueue(
    tasks,
    queue: str,
    *,
    call_id: str | None,
    phone: str | None = None,
    name: str | None = None,
) -> bool:
    """Queue ``/tasks/transcript-translate`` for a call (and, with ``phone``, the
    by-text conversation that belongs to it). Never raises: a call or a report is
    never failed over translation. True when a task was queued."""
    import logging

    if not call_id or tasks is None or not queue:
        return False
    payload: dict[str, Any] = {"call_id": call_id}
    if phone:
        payload["phone"] = phone
    try:
        return bool(await tasks.enqueue(queue, TASK_PATH, payload, name=name))
    except Exception:
        logging.getLogger(__name__).exception(
            "translation enqueue failed", extra={"event": "translate.enqueue_err"}
        )
        return False


# --------------------------------------------------------------------------- outbound (demo)
#
# English -> a patient's language, for the demo auto-approve only
# (``languages.auto_approved``; docs/LANGUAGES-PLAN.md, "Language availability
# and approval"): a text or a voicemail in a language with no language pack is
# the English wording, machine-translated when it is used. Unreviewed wording.


class OutboundTranslator(Protocol):
    model: str

    async def translate_out(self, texts: list[str], *, target: str) -> list[str]: ...


@dataclass(frozen=True)
class Outbound:
    """What to send: the translation, or the English when translation failed or
    dropped a literal the carrier or emergency services need (``error`` says
    which). ``source``: "machine" or "english_fallback"."""

    text: str
    source: str
    error: str | None = None

    @property
    def translated(self) -> bool:
        return self.source == "machine"


#: (English text, language) -> translation. In memory per process, never in a
#: database: an outbound text can carry a patient's first name.
_OUT_CACHE: dict[tuple[str, str], str] = {}
_OUT_CACHE_MAX = 2000


def literal_problems(english: str, translated: str) -> list[str]:
    """STOP, 911, tel:911 and the reply letter C must survive a translation
    wherever the English has them (the pack lint's rule, applied at send time)."""
    from .language_packs import _literal_problems

    return _literal_problems("translation", translated, english)


async def translate_outbound(translator: Any, text: str, language: str) -> Outbound:
    """``text`` (English) in ``language``, cached by (text, language). Never
    raises: on any failure, or when a required literal did not survive, the
    English text comes back with the reason. A partly translated mix is never
    returned."""
    import logging

    key = (text, language)
    if key in _OUT_CACHE:
        return Outbound(_OUT_CACHE[key], "machine")
    log = logging.getLogger(__name__)
    try:
        out = await translator.translate_out([text], target=language)
        if len(out) != 1 or not str(out[0] or "").strip():
            raise TranslationFailed("empty translation")
        translated = str(out[0]).strip()
    except Exception as exc:  # noqa: BLE001 - every failure falls back to English
        reason = f"translation failed ({type(exc).__name__})"
        log.warning(
            "outbound translation failed; English sent",
            extra={"event": "translate.out_failed", "language": language, "error": reason},
        )
        return Outbound(text, "english_fallback", reason)
    problems = literal_problems(text, translated)
    if problems:
        reason = "translation dropped a required literal: " + "; ".join(
            p.removeprefix("translation: ") for p in problems
        )
        log.warning(
            "outbound translation dropped a literal; English sent",
            extra={"event": "translate.out_literal", "language": language, "error": reason},
        )
        return Outbound(text, "english_fallback", reason)
    if len(_OUT_CACHE) >= _OUT_CACHE_MAX:
        _OUT_CACHE.pop(next(iter(_OUT_CACHE)))
    _OUT_CACHE[key] = translated
    return Outbound(translated, "machine")


def clear_outbound_cache() -> None:
    _OUT_CACHE.clear()
