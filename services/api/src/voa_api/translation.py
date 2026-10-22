"""``/tasks/transcript-translate``: bilingual transcripts (docs/LANGUAGES-PLAN.md
§3.6, step 2).

After a call the gateway queues this task; after a by-text report is filed the
SMS service queues it with the patient's ``phone``. It:

* reads ``transcripts/{call_id}`` and adds ``lang`` and ``text_en`` to every
  turn (agent and patient), plus ``translation`` {status: done|failed|skipped,
  model, at, languages, error?}. The original ``text`` is never changed;
* sets ``language_used`` (the language most of the patient's turns were in,
  en-US for English or none) and ``languages_spoken`` on the call doc;
* with ``phone``, adds ``lang`` and ``body_en`` to the call's text messages.

Every transcript with at least one patient turn is translated (step 3): an
English-looking transcript is NOT skipped any more, because unaccented Spanish
("dolor de pecho") can look English and skipping it would bypass the re-check.
Only a transcript with no patient turn and nothing but English-looking text is
skipped without a model call.

Idempotent: a transcript whose translation is done or skipped is left alone
(also inside the transaction); a failed one is tried again on the next
delivery. The translation itself never touches ``follow_up``, the triage or the
answers. A failure leaves the original text intact with status "failed":
nothing is ever made up.

Step 3 (the safety layer, docs/LANGUAGES-PLAN.md §3.3): once the translation is
stored, :func:`after_translation` re-checks the English of what the patient said
and may RAISE the call's follow-up (``voa_core.language_safety``), never lower it.
"""

from __future__ import annotations

import logging
from typing import Any

from voa_core import lifecycle, translate
from voa_core.store import Where, now_iso

from .context import CALL_ID_RE, CALLS, TRANSCRIPTS, Api

log = logging.getLogger(__name__)

MESSAGES = "sms_messages"
MAX_ERROR = 300


async def after_translation(
    api: Api, call_id: str, turns: list[dict[str, Any]], translation: dict[str, Any]
) -> dict[str, Any]:
    """Step 3 (docs/LANGUAGES-PLAN.md §3.3): called right after a transcript's
    translation is stored (status done), with the translated turns. Re-checks the
    English of what the patient said and RAISES the call's follow-up when that is
    more urgent; never lowers a tier or clears a follow-up. Never raises."""
    try:
        return await recheck(api, call_id, turns)
    except Exception:
        log.exception("translation re-check failed", extra={"event": "translation_check.error"})
        return {"error": True}


async def unreadable_transcript(
    api: Api, call_id: str, turns: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The translation failed. When the patient seems to have spoken another
    language (the hint, used in the safe direction, or the call's language), the
    re-check cannot run: raise to same day for a nurse to read it (status
    "failed": a later successful delivery still runs the full re-check)."""
    from voa_core import language_safety as ls

    try:
        call = await api.store.get(CALLS, call_id)
        if call is None:
            return None
        patient = [str(t.get("text") or "") for t in turns if t.get("role") == "patient"]
        foreign = any(not translate.looks_english(t) for t in patient)
        if not (foreign or ls.spoke_other_language([], call)):
            return None
        lang = call.get("language_used") or call.get("language")
        finding = ls.unreadable_finding(
            lang if lang and lang != "en-US" else None, "the transcript"
        )
        now = now_iso(api.now())
        out: dict[str, Any] = {}

        def mutate(cur: dict | None) -> dict | None:
            out.clear()
            if cur is None or (cur.get("translation_check") or {}).get("status"):
                return None
            doc, withdraw = ls.apply(cur, [finding], now=now, status="failed")
            out.update(doc["translation_check"], withdraw=withdraw)
            return doc

        await api.store.transact(CALLS, call_id, mutate)
        if out.get("withdraw"):
            from voa_core import review

            from .context import EHR_OUTBOX

            await api.store.merge(
                EHR_OUTBOX,
                call_id,
                review.withdrawn_export(now, "the transcript could not be read"),
            )
        if out.get("raised_to"):
            log.warning(
                "follow-up raised: transcript could not be translated",
                extra={"event": "translation_check.unreadable", "to": out["raised_to"]},
            )
        return out or None
    except Exception:
        log.exception(
            "unreadable-transcript flag failed", extra={"event": "translation_check.error"}
        )
        return None


def _campaign_for(call: dict[str, Any]):
    from voa_core import survey

    try:
        return survey.campaign_for(call.get("campaign_id"))
    except Exception:  # noqa: BLE001 - an unknown use case still gets the transcript check
        return None


async def recheck(api: Api, call_id: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    """The re-check itself (see :mod:`voa_core.language_safety`)."""
    from voa_core import language_safety as ls

    call = await api.store.get(CALLS, call_id)
    if call is None:
        return {"skipped": "no call"}
    if (call.get("translation_check") or {}).get("status") in ls.FINAL_STATUSES:
        return {"skipped": "already checked"}
    if not ls.spoke_other_language(turns, call):
        return {"skipped": "English"}

    findings: list[ls.Finding] = ls.transcript_findings(turns)
    campaign = _campaign_for(call)
    answers = ls.answer_texts(call, campaign)
    swelling = ls.swelling_fields(campaign)
    questions = ls.questions_of(call)
    q_texts = {
        i: str(q.get("text_original") or q.get("text") or "")
        for i, q in enumerate(questions)
        if str(q.get("text_original") or q.get("text") or "").strip()
    }
    status, note = "done", None
    new_questions: list[dict[str, Any]] | None = None
    batch = [*answers.values(), *q_texts.values()]
    if batch:
        try:
            results = await api.get_translator().translate_turns(batch, target="en")
            if len(results) != len(batch):
                raise translate.TranslationFailed("wrong number of translations")
        except Exception as exc:  # noqa: BLE001 - fail safe: a nurse reads them
            log.warning(
                "answer translation failed",
                extra={"event": "translation_check.translate_failed", "error": type(exc).__name__},
            )
            status, note = "partial", f"{type(exc).__name__}"
            lang = call.get("language_used") or call.get("language")
            findings.append(ls.unreadable_finding(lang))
        else:
            n = len(answers)
            for (field, original), r in zip(answers.items(), results[:n], strict=True):
                english = r.text_en or original
                key = field.removeprefix("_raw.")
                findings.extend(
                    ls.english_findings(
                        english, f"translated answer ({key})", swelling=key in swelling
                    )
                )
            q_en = {
                i: (r.text_en or text)
                for (i, text), r in zip(q_texts.items(), results[n:], strict=True)
                if r.text_en
            }
            if q_en:
                new_questions, q_found = ls.question_findings(questions, q_en)
                findings.extend(q_found)

    now = now_iso(api.now())
    out: dict[str, Any] = {}

    def mutate(cur: dict | None) -> dict | None:
        out.clear()
        if cur is None or (cur.get("translation_check") or {}).get("status") in ls.FINAL_STATUSES:
            return None  # deleted, or a duplicate delivery got there first
        doc, withdraw = ls.apply(
            cur, findings, now=now, questions=new_questions, status=status, note=note
        )
        out.update(doc["translation_check"], withdraw=withdraw)
        return doc

    await api.store.transact(CALLS, call_id, mutate)
    if not out:
        return {"skipped": "already checked"}
    if out.get("withdraw"):
        from voa_core import review

        from .context import EHR_OUTBOX

        await api.store.merge(
            EHR_OUTBOX,
            call_id,
            review.withdrawn_export(now, "the translated transcript raised the follow-up"),
        )
    if out.get("raised_to"):
        log.warning(
            "follow-up raised by the translated transcript",
            extra={
                "event": "translation_check.raised",
                "from": out.get("from"),
                "to": out["raised_to"],
                "kinds": sorted({f["kind"] for f in out.get("findings") or []}),
                "reopened": out.get("reopened"),
            },
        )
        try:
            from voa_core.store import new_id

            await api.store.set(
                "audit_log",
                new_id(),
                {
                    "at": now,
                    "by": ls.SOURCE,
                    "action": "translation_check.raised",
                    "target": {"call_id": call_id},
                    "from": out.get("from"),
                    "to": out["raised_to"],
                    "reopened": bool(out.get("reopened")),
                },
            )
        except Exception:  # never fail the raise over its audit line
            log.exception("audit write failed", extra={"event": "audit.write_failed"})
    return {
        "status": out.get("status"),
        "findings": len(out.get("findings") or []),
        "raised_to": out.get("raised_to"),
    }


def _language_patch(turns: list[dict[str, Any]]) -> dict[str, Any]:
    patient = [t.get("lang") for t in turns if t.get("role") == "patient"]
    spoken = translate.spoken_languages(turns)
    return {
        "language_used": translate.majority_language(patient),
        "languages_spoken": spoken or [translate.majority_language(patient)],
    }


async def _set_call_languages(api: Api, call_id: str, patch: dict[str, Any]) -> None:
    now = api.now()
    await api.store.transact(
        CALLS,
        call_id,
        # Never create the doc: only describe a call that still exists.
        lambda cur: None if cur is None else lifecycle.merge_call_doc(cur, patch, now=now),
    )


async def translate_transcript(api: Api, call_id: str) -> dict[str, Any]:
    doc = await api.store.get(TRANSCRIPTS, call_id)
    turns = [t for t in (doc or {}).get("turns") or [] if isinstance(t, dict)]
    if not turns:
        return {"skipped": "no transcript"}
    if (doc.get("translation") or {}).get("status") in ("done", "skipped"):
        return {"skipped": "already translated"}
    texts = [str(t.get("text") or "") for t in turns]
    at = now_iso(api.now())

    if translate.all_english(texts) and not any(t.get("role") == "patient" for t in turns):
        # No patient turn (an agent-only voicemail): nothing to re-check. Any
        # transcript where the patient spoke is translated whatever it looks
        # like: looks_english is a hint, never a gate (step 3).
        new_turns = None
        translation = {
            "status": "skipped",
            "reason": "no patient turn, all English",
            "model": None,
            "at": at,
            "languages": [translate.languages.DEFAULT],
        }
    else:
        translator = api.get_translator()
        model = getattr(translator, "model", None)
        try:
            results = await translator.translate_turns(texts, target="en")
            new_turns = translate.apply_to_turns(turns, results)
        except Exception as exc:  # noqa: BLE001 - recorded, original kept
            log.warning(
                "transcript translation failed",
                extra={"event": "translate.failed", "error": type(exc).__name__},
            )
            new_turns = None
            translation = {
                "status": "failed",
                "model": model,
                "at": at,
                "languages": [],
                "error": f"{type(exc).__name__}: {exc}"[:MAX_ERROR],
            }
        else:
            translation = {
                "status": "done",
                "model": model,
                "at": at,
                "languages": list(dict.fromkeys(t["lang"] for t in new_turns)),
            }

    stored: list[bool] = []

    def apply(cur: dict | None) -> dict | None:
        stored.clear()
        if cur is None:
            return None
        if (cur.get("translation") or {}).get("status") in ("done", "skipped"):
            return None  # a duplicate delivery got there first
        cur_turns = cur.get("turns") or []
        if new_turns is not None and [str(t.get("text") or "") for t in cur_turns] != texts:
            return None  # the transcript changed under us: the next delivery redoes it
        stored.append(True)
        out = {**cur, "translation": translation}
        if new_turns is not None:
            out["turns"] = new_turns
        return out

    await api.store.transact(TRANSCRIPTS, call_id, apply)
    if not stored:
        return {"skipped": "already translated"}
    if translation["status"] != "failed":
        await _set_call_languages(api, call_id, _language_patch(new_turns or turns))
    check = None
    if translation["status"] == "done" and new_turns is not None:
        check = await after_translation(api, call_id, new_turns, translation)
    elif translation["status"] == "failed":
        check = await unreadable_transcript(api, call_id, turns)
    log.info(
        "transcript translation stored",
        extra={
            "event": "translate.stored",
            "status": translation["status"],
            "turns": len(turns),
            "languages": translation["languages"],
        },
    )
    result: dict[str, Any] = {
        "status": translation["status"],
        "languages": translation["languages"],
    }
    if isinstance(check, dict) and check.get("raised_to"):
        result["raised_to"] = check["raised_to"]
    return result


async def translate_messages(api: Api, phone: str, call_id: str) -> dict[str, Any]:
    """The by-text conversation for one call: ``lang`` and ``body_en`` on every
    message not translated yet. An all-English-looking thread is left as it is."""
    rows = [
        (i, d)
        for i, d in await api.store.query(MESSAGES, [Where("phone", "==", phone)], limit=1000)
        if d.get("call_sid") == call_id and not d.get("lang")
    ]
    if not rows:
        return {"skipped": "nothing to translate"}
    rows.sort(key=lambda r: r[1].get("at") or "")
    bodies = [str(d.get("body") or "") for _, d in rows]
    if translate.all_english(bodies):
        return {"skipped": "all English"}
    translator = api.get_translator()
    try:
        results = await translator.translate_turns(bodies, target="en")
        if len(results) != len(rows):
            raise translate.TranslationFailed("wrong number of translations")
    except Exception as exc:  # noqa: BLE001 - the originals stay as they are
        log.warning(
            "text translation failed",
            extra={"event": "translate.sms_failed", "error": type(exc).__name__},
        )
        return {"status": "failed"}
    for (doc_id, d), r in zip(rows, results, strict=True):
        body = str(d.get("body") or "")

        def patch(cur, body=body, r=r):
            if cur is None or cur.get("lang") or str(cur.get("body") or "") != body:
                return None
            return {**cur, "lang": translate.to_supported(r.lang), "body_en": r.text_en}

        await api.store.transact(MESSAGES, doc_id, patch)
    return {"status": "done", "messages": len(rows)}


async def run(api: Api, payload: dict) -> dict[str, Any]:
    call_id = str(payload.get("call_id") or "")
    if not CALL_ID_RE.match(call_id):
        return {"skipped": "bad call id"}
    out: dict[str, Any] = {"transcript": await translate_transcript(api, call_id)}
    phone = str(payload.get("phone") or "")
    if phone:
        out["texts"] = await translate_messages(api, phone, call_id)
    return out
