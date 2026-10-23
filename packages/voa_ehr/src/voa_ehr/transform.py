"""Pure mapping: an ``ehr_outbox`` record + a profile + a patient reference -> FHIR.

No I/O, no clock: everything comes in as arguments, so the console's preview and
the exporter produce byte-for-byte the same JSON.

What goes in:

* the outbox record (``voa_core.review.ehr_payload`` plus ``status`` etc.): its
  ``questionnaire_response.item[{linkId, answer}]`` carries the app's question
  ids as linkId and the raw answers the nurse signed off;
* the use-case mapping chosen from the profile (``Profile.for_use_case``);
* the patient reference the exporter resolved (``Patient/<id>``);
* optionally the transcript turns, which the exporter loads ONLY when the
  profile turns the transcript document on.

What never goes out: the date of birth (it is not in the record and is refused
if it ever appears as an answer), the transcript unless enabled, the summary
unless enabled.
"""

from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from voa_core.campaign import norm_answer
from voa_core.review import NEEDS_TIERS

from .profile import (
    Coding,
    DocumentMap,
    ObservationMap,
    Profile,
    UseCaseMap,
    ValueSpec,
)

Doc = dict[str, Any]

#: Keys never sent whatever a profile says (defence in depth: the outbox record
#: has no DOB today, and must not grow one by accident).
NEVER_SEND: frozenset[str] = frozenset({"patient_dob", "dob", "date_of_birth", "birth_date"})

OBS_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
ENTERED_IN_ERROR = "entered-in-error"
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


class Unmappable(ValueError):
    """An answer the spec cannot type (e.g. "unknown" for a boolean)."""


@dataclass
class Entry:
    resource_type: str
    #: identifier.value (the system is the profile's identifiers.system)
    ident: str
    full_url: str
    resource: Doc


@dataclass
class ExportBundle:
    entries: list[Entry]
    bundle: Doc
    #: [{"question": id, "reason": ...}] answers not sent, and why.
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: [{source, target, question_id?, note?}]: where each mapped value came from.
    #: ``source`` is a JSON Pointer into the outbox record (None when the value
    #: comes from outside it: the transcript doc, or the call doc's answers);
    #: ``target`` is a JSON Pointer into ``bundle``. Built alongside the bundle,
    #: never changes it.
    source_map: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resources(self) -> list[Doc]:
        return [e.resource for e in self.entries]


# --------------------------------------------------------------------------- answers


def answers_from(record: Mapping[str, Any], call: Mapping[str, Any] | None = None) -> Doc:
    """The signed-off answers, by app question id.

    The record's questionnaire_response items are what the nurse signed; the
    call's survey_responses are the fallback for records written without them.
    Keys starting with ``_`` (engine bookkeeping) and NEVER_SEND keys are dropped.
    """
    items = ((record.get("questionnaire_response") or {}).get("item")) or []
    out: Doc = {}
    for it in items:
        if isinstance(it, Mapping) and it.get("linkId") is not None:
            out[str(it["linkId"])] = it.get("answer")
    if not items and call:
        out = dict(call.get("survey_responses") or {})
    return {
        k: v for k, v in out.items() if not str(k).startswith("_") and k.lower() not in NEVER_SEND
    }


def pointer(*parts: Any) -> str:
    """A JSON Pointer (RFC 6901) from path parts: ``pointer("a", 0, "b/c")`` is
    ``/a/0/b~1c``."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _ptr(base: str | None, *parts: Any) -> str | None:
    return None if base is None else base + pointer(*parts)


def answer_sources(record: Mapping[str, Any]) -> dict[str, str]:
    """App question id -> the JSON Pointer of its answer in the outbox record
    (``/questionnaire_response/item/<i>/answer``). The last item wins, as in
    :func:`answers_from`. Answers taken from the call doc have no pointer."""
    items = ((record.get("questionnaire_response") or {}).get("item")) or []
    out: dict[str, str] = {}
    for i, it in enumerate(items):
        if isinstance(it, Mapping) and it.get("linkId") is not None:
            out[str(it["linkId"])] = pointer("questionnaire_response", "item", i, "answer")
    return out


def _answered(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list | dict):
        return bool(v)
    return True


#: Whether an answer counts as given (not None, blank, or empty).
answered = _answered


def _key(v: Any) -> str:
    return norm_answer(v)


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _typed(spec: ValueSpec, v: Any) -> Doc:
    """One value -> {"value<Type>": ...} (QuestionnaireResponse answer shape)."""
    t = spec.type
    if t == "boolean":
        if isinstance(v, bool):
            return {"valueBoolean": v}
        k = _key(v)
        if k in ("yes", "no"):
            return {"valueBoolean": k == "yes"}
        raise Unmappable(f"{v!r} is not yes or no")
    if t in ("integer", "decimal", "quantity"):
        n = _number(v)
        if n is None:
            raise Unmappable(f"{v!r} is not a number")
        if t == "integer":
            if n != int(n):
                raise Unmappable(f"{v!r} is not a whole number")
            return {"valueInteger": int(n)}
        num: int | float = int(n) if n == int(n) else n
        if t == "decimal":
            return {"valueDecimal": num}
        return {
            "valueQuantity": {
                "value": num,
                "unit": spec.unit,
                "system": spec.unit_system,
                "code": spec.unit_code or spec.unit,
            }
        }
    if t == "string":
        s = " ".join(str(v).split())
        if not s:
            raise Unmappable("empty text")
        return {"valueString": s}
    if t == "coding":
        c = spec.codes.get(_key(v))
        if c is None:
            raise Unmappable(f"{v!r} has no code")
        return {"valueCoding": _coding(c, spec.system)}
    if t == "date":
        s = str(v).strip()
        for f in spec.date_formats:
            try:
                return {"valueDate": datetime.strptime(s, f).date().isoformat()}  # noqa: DTZ007
            except ValueError:
                continue
        raise Unmappable(f"{v!r} is not a date in {spec.date_formats}")
    raise Unmappable(f"unsupported type {t}")  # pragma: no cover - Literal guards it


def _coding(c: Coding, default_system: str) -> Doc:
    out: Doc = {"system": c.system or default_system, "code": c.code}
    if c.display:
        out["display"] = c.display
    return out


def values_for(
    spec: ValueSpec, qid: str, answers: Mapping[str, Any]
) -> tuple[list[Doc], list[Any]]:
    """(typed values, raw values that could not be typed) for one mapping.

    Nothing (([], [])) when the question was not answered. Transforms, in order:
    constant | concat, then value_map, then the type.
    """
    typed, bad = _traced_values(spec, qid, answers, {})
    return [t for t, _ in typed], [b for b, _ in bad]


#: (value, the JSON Pointers of the answers it came from)
_Traced = tuple[Any, list[str | None]]


def _traced_values(
    spec: ValueSpec, qid: str, answers: Mapping[str, Any], srcs: Mapping[str, str]
) -> tuple[list[_Traced], list[_Traced]]:
    """:func:`values_for`, with each value's source pointers (``srcs``: qid ->
    the answer's pointer, from :func:`answer_sources`)."""
    raw = answers.get(qid)
    vals: list[_Traced]
    if spec.concat:
        parts = [(answers.get(c), srcs.get(c)) for c in spec.concat]
        hits = [(" ".join(str(p).split()), s) for p, s in parts if _answered(p)]
        if not hits:
            return [], []
        vals = [(spec.separator.join(t for t, _ in hits), [s for _, s in hits])]
    elif not _answered(raw):
        return [], []
    elif spec.constant is not None:
        vals = [(spec.constant, [srcs.get(qid)])]
    else:
        base = srcs.get(qid)
        listed = isinstance(raw, list)
        vals = []
        for k, v in enumerate(raw if listed else [raw]):
            src = _ptr(base, k) if listed else base
            # patient_questions: [{text, category, priority}] -> the question text.
            if isinstance(v, Mapping):
                v, src = v.get("text"), _ptr(src, "text")
            if _answered(v):
                vals.append((v, [src]))
    typed: list[_Traced] = []
    bad: list[_Traced] = []
    for v, src in vals:
        if spec.value_map and not spec.concat:
            v = spec.value_map.get(_key(v), v)
        try:
            typed.append((_typed(spec, v), src))
        except Unmappable:
            bad.append((v, src))
    return typed, bad


# --------------------------------------------------------------------------- helpers


def token(system: str, value: str) -> str:
    """identifier search token, URL-encoded (``|`` included)."""
    return quote(f"{system}|{value}", safe=":/")


def full_url(system: str, value: str) -> str:
    """Deterministic urn:uuid, so previews and re-runs produce identical bundles."""
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{system}|{value}')}"


def _request(profile: Profile, rtype: str, system: str, value: str) -> Doc:
    if profile.target.write_mode == "conditional_create":
        return {"method": "POST", "url": rtype, "ifNoneExist": f"identifier={token(system, value)}"}
    return {"method": "PUT", "url": f"{rtype}?identifier={token(system, value)}"}


def _dt(*candidates: Any) -> str | None:
    for c in candidates:
        if isinstance(c, str) and _ISO_DT.match(c):
            return c
    return None


def _dt_src(record: Mapping[str, Any], *paths: tuple[str, ...]) -> str | None:
    """The pointer of the candidate :func:`_dt` picks from these record paths."""
    for path in paths:
        cur: Any = record
        for p in path:
            cur = cur.get(p) if isinstance(cur, Mapping) else None
        if isinstance(cur, str) and _ISO_DT.match(cur):
            return pointer(*path)
    return None


class _Trace:
    """Source-map entries for one resource; targets are relative to the resource
    until :meth:`rebase` puts them under ``/entry/<i>/resource``."""

    def __init__(self, record: Mapping[str, Any], call_id: str):
        self.record = record
        self.call_id = call_id
        self.rows: list[dict[str, Any]] = []

    def add(
        self,
        source: str | None,
        target: str,
        question_id: str | None = None,
        note: str | None = None,
    ) -> None:
        row: dict[str, Any] = {"source": source, "target": target}
        if question_id is not None:
            row["question_id"] = question_id
        if note:
            row["note"] = note
        self.rows.append(row)

    def has(self, *path: str) -> str | None:
        """The pointer of a record path when the record has that key."""
        cur: Any = self.record
        for p in path:
            if not isinstance(cur, Mapping) or p not in cur:
                return None
            cur = cur[p]
        return pointer(*path)

    def when(self, target: str, *path: str, note: str | None = None) -> None:
        src = self.has(*path)
        if src is not None:
            self.add(src, target, note=note)

    def subject(self, *targets: str) -> None:
        src = self.has("patient", "mrn")
        if src is not None:
            for t in targets:
                self.add(src, t, note="patient lookup (placeholder in preview)")

    def identifier(self, target: str) -> None:
        src = "/call_sid" if self.record.get("call_sid") == self.call_id else None
        self.add(src, target, note="identifier from the call id")

    def dt(self, target: str, *paths: tuple[str, ...]) -> None:
        src = _dt_src(self.record, *paths)
        if src is not None:
            self.add(src, target)

    def rebase(self, index: int) -> list[dict[str, Any]]:
        base = pointer("entry", index, "resource")
        return [{**r, "target": base + r["target"]} for r in self.rows]


def _value_key(typed: Mapping[str, Any]) -> str:
    return next(iter(typed))


def _how(spec: ValueSpec) -> str:
    how = [spec.type]
    if spec.constant is not None:
        how.append("constant")
    if spec.concat:
        how.append("concat " + ", ".join(spec.concat))
    if spec.value_map and not spec.concat:
        how.append("value map")
    return "; ".join(how)


def _concat_ids(spec: ValueSpec, qid: str) -> list[str]:
    return list(spec.concat) if spec.concat else [qid]


def identifier_values(call_id: str, uc: UseCaseMap) -> list[tuple[str, str]]:
    """(resourceType, identifier value) of everything this mapping can emit, for
    withdrawals: each is searched and marked entered-in-error when found."""
    out: list[tuple[str, str]] = []
    if uc.questionnaire_response and uc.questionnaire_response.enabled:
        out.append(("QuestionnaireResponse", call_id))
    if uc.task and uc.task.enabled:
        out.append(("Task", f"{call_id}-task"))
    for ob in uc.observations:
        out.append(("Observation", f"{call_id}-obs-{ob.source}"))
    for name, doc in (("summary", uc.summary), ("transcript", uc.transcript)):
        if doc.enabled:
            out.append((doc.resource, f"{call_id}-{name}"))
    return out


def entered_in_error(resource: Mapping[str, Any]) -> Doc:
    """The same resource, withdrawn. Every type we emit has this status code."""
    out = dict(resource)
    out["status"] = ENTERED_IN_ERROR
    if out.get("resourceType") == "DocumentReference":
        out.pop("docStatus", None)
    return out


# --------------------------------------------------------------------------- resources


def _qr(
    record: Mapping[str, Any],
    uc: UseCaseMap,
    answers: Mapping[str, Any],
    subject: Doc,
    ident: Doc,
    skipped: list[dict[str, str]],
    trace: _Trace | None = None,
) -> Doc | None:
    qr = uc.questionnaire_response
    if qr is None or not qr.enabled:
        return None
    tr = trace or _Trace({}, "")
    srcs = answer_sources(tr.record)
    items: list[Doc] = []
    excluded = set(qr.exclude)

    def item_sources(qids: list[str], j: int, note: str) -> None:
        for q in qids:
            src = srcs.get(q)
            tr.add(src[: -len("/answer")] if src else None, pointer("item", j), q, note)

    for qid, m in qr.items.items():
        if qid in excluded:
            continue
        typed, bad = _traced_values(m, qid, answers, srcs)
        if typed:
            item: Doc = {"linkId": m.link_id}
            if m.text:
                item["text"] = m.text
            item["answer"] = [t for t, _ in typed]
            j = len(items)
            item_sources(_concat_ids(m, qid), j, f"linkId {m.link_id}")
            for k, (t, ss) in enumerate(typed):
                for src in ss:
                    tr.add(src, pointer("item", j, "answer", k, _value_key(t)), qid, _how(m))
            items.append(item)
        for b, ss in bad:
            if qr.unmapped == "text":
                j = len(items)
                items.append(
                    {
                        "linkId": f"{qr.unmapped_prefix}{qid}",
                        "answer": [{"valueString": " ".join(str(b).split())}],
                    }
                )
                note = "could not be typed; sent as text"
                item_sources(_concat_ids(m, qid), j, note)
                for src in ss:
                    tr.add(src, pointer("item", j, "answer", 0, "valueString"), qid, note)
                skipped.append({"question": qid, "reason": "could not be typed; sent as text"})
            else:
                skipped.append({"question": qid, "reason": f"could not be typed ({m.type})"})
    concat_sources = {c for m in qr.items.values() for c in m.concat}
    for qid, v in answers.items():
        if qid in qr.items or qid in excluded or not _answered(v):
            continue
        if qid in concat_sources:
            continue
        if qr.unmapped == "text":
            listed = isinstance(v, list)
            vals: list[tuple[Any, str | None]] = []
            for k, x in enumerate(v if listed else [v]):
                src = _ptr(srcs.get(qid), k) if listed else srcs.get(qid)
                if isinstance(x, Mapping):
                    x, src = x.get("text"), _ptr(src, "text")
                if _answered(x):
                    vals.append((x, src))
            ans = [{"valueString": " ".join(str(x).split())} for x, _ in vals]
            if ans:
                j = len(items)
                items.append({"linkId": f"{qr.unmapped_prefix}{qid}", "answer": ans})
                item_sources([qid], j, "not mapped; sent as text")
                for k, (_, src) in enumerate(vals):
                    tr.add(
                        src,
                        pointer("item", j, "answer", k, "valueString"),
                        qid,
                        "not mapped; sent as text",
                    )
                skipped.append({"question": qid, "reason": "not mapped; sent as text"})
        else:
            skipped.append({"question": qid, "reason": "not mapped"})
    enc = record.get("encounter") or {}
    rev = record.get("review") or {}
    res: Doc = {
        "resourceType": "QuestionnaireResponse",
        "identifier": ident,
        "questionnaire": qr.questionnaire,
        "status": "completed",
        "subject": subject,
    }
    authored = _dt(enc.get("date"), rev.get("at"), record.get("created_at"))
    if authored:
        res["authored"] = authored
        tr.dt("/authored", ("encounter", "date"), ("review", "at"), ("created_at",))
    if qr.author_display:
        res["author"] = {"display": qr.author_display}
    if items:
        res["item"] = items
    tr.identifier("/identifier/value")
    tr.subject("/subject")
    return res


def _task(
    record: Mapping[str, Any],
    uc: UseCaseMap,
    subject: Doc,
    ident: list[Doc],
    focus: str | None,
    trace: _Trace | None = None,
) -> Doc | None:
    t = uc.task
    if t is None or not t.enabled:
        return None
    tr = trace or _Trace({}, "")
    rev = record.get("review") or {}
    disp = record.get("disposition") or {}
    tier = disp.get("follow_up") or "none"
    resolved = bool(rev.get("resolved"))
    if t.emit_when == "resolved" and not resolved:
        return None
    if t.emit_when == "needs_follow_up" and not (resolved or tier in NEEDS_TIERS):
        return None
    res: Doc = {
        "resourceType": "Task",
        "identifier": ident,
        "status": t.status["resolved" if resolved else "open"],
        "intent": t.intent,
        "priority": t.priority.get(tier, "routine"),
        "description": t.description,
        "for": subject,
    }
    tr.identifier("/identifier/0/value")
    tr.when("/status", "review", "resolved", note=f"resolved -> {res['status']}")
    tr.when("/priority", "disposition", "follow_up", note=f"tier {tier} -> {res['priority']}")
    tr.subject("/for")
    if t.code:
        res["code"] = {"coding": [_coding(t.code, t.code_system)]}
    if focus:
        res["focus"] = {"reference": focus}
    authored = _dt(record.get("created_at"), rev.get("at"))
    if authored:
        res["authoredOn"] = authored
        tr.dt("/authoredOn", ("created_at",), ("review", "at"))
    modified = _dt(rev.get("at"))
    if modified:
        res["lastModified"] = modified
        tr.when("/lastModified", "review", "at")
    signer = rev.get("by")
    if signer and t.owner.mode == "display":
        res["owner"] = {"display": str(signer)}
        tr.when("/owner", "review", "by", note="owner: display")
    elif signer and t.owner.mode == "identifier":
        res["owner"] = {
            "identifier": {"system": t.owner.identifier_system, "value": str(signer)},
            "display": str(signer),
        }
        tr.when("/owner", "review", "by", note="owner: identifier")
    if t.include_reason and disp.get("reason"):
        res["reasonCode"] = {"text": str(disp["reason"])}
        tr.when("/reasonCode/text", "disposition", "reason")
    if t.include_nurse_note and rev.get("note"):
        note: Doc = {"text": str(rev["note"])}
        tr.when("/note/0/text", "review", "note", note="the nurse's note")
        if modified:
            note["time"] = modified
            tr.when("/note/0/time", "review", "at")
        if signer:
            note["authorString"] = str(signer)
            tr.when("/note/0/authorString", "review", "by")
        res["note"] = [note]
    return res


#: QuestionnaireResponse answer key -> Observation value key.
_OBS_VALUE = {
    "valueBoolean": "valueBoolean",
    "valueInteger": "valueInteger",
    "valueDecimal": "valueQuantity",
    "valueString": "valueString",
    "valueQuantity": "valueQuantity",
    "valueDate": "valueDateTime",
}


def _obs(
    record: Mapping[str, Any],
    ob: ObservationMap,
    answers: Mapping[str, Any],
    subject: Doc,
    ident: list[Doc],
    derived: str | None,
    skipped: list[dict[str, str]],
    trace: _Trace | None = None,
) -> Doc | None:
    tr = trace or _Trace({}, "")
    typed_src, bad = _traced_values(ob, ob.source, answers, answer_sources(tr.record))
    typed = [t for t, _ in typed_src]
    if bad and not typed:
        skipped.append({"question": ob.source, "reason": f"observation: not a {ob.type}"})
    if not typed:
        return None
    ((k, v),) = typed[0].items()
    if k == "valueCoding":
        value: Doc = {"valueCodeableConcept": {"coding": [v]}}
        target = "/valueCodeableConcept/coding/0"
    elif k == "valueDecimal":
        value = {"valueQuantity": {"value": v}}
        target = "/valueQuantity/value"
    else:
        value = {_OBS_VALUE[k]: v}
        target = pointer(_OBS_VALUE[k])
    note = f"observation {ob.code.code}; {_how(ob)}"
    for src in typed_src[0][1]:
        tr.add(src, target, ob.source, note)
    tr.identifier("/identifier/0/value")
    tr.subject("/subject", "/performer/0")
    enc = record.get("encounter") or {}
    res: Doc = {
        "resourceType": "Observation",
        "identifier": ident,
        "status": "final",
        "category": [{"coding": [{"system": OBS_CATEGORY_SYSTEM, "code": ob.category}]}],
        "code": {"coding": [_coding(ob.code, ob.code_system)]},
        "subject": subject,
        # Patient-reported: the patient is the performer.
        "performer": [subject],
        **value,
    }
    eff = _dt(enc.get("date"), (record.get("review") or {}).get("at"))
    if eff:
        res["effectiveDateTime"] = eff
        tr.dt("/effectiveDateTime", ("encounter", "date"), ("review", "at"))
    if derived:
        res["derivedFrom"] = [{"reference": derived}]
    return res


def summary_text(record: Mapping[str, Any]) -> str:
    enc = record.get("encounter") or {}
    disp = record.get("disposition") or {}
    rev = record.get("review") or {}
    lines = [f"Automated outreach by {enc.get('channel') or 'phone'} on {enc.get('date') or '?'}."]
    lines.append(f"Follow-up tier: {disp.get('follow_up') or 'none'}.")
    if disp.get("reason"):
        lines.append(f"Reason: {disp['reason']}")
    if disp.get("changed_by_nurse"):
        lines.append("The tier was changed by a nurse.")
    if rev.get("by"):
        lines.append(f"Signed off by {rev['by']} at {rev.get('at') or '?'}.")
    if rev.get("note"):
        lines.append(f"Nurse note: {rev['note']}")
    return "\n".join(lines)


def transcript_text(turns: list[Mapping[str, Any]], language: str) -> str:
    out = []
    for t in turns or []:
        text = t.get("text_en") if language == "english" and t.get("text_en") else t.get("text")
        if not text:
            continue
        role = str(t.get("role") or "unknown").capitalize()
        out.append(f"{role}: {' '.join(str(text).split())}")
    return "\n".join(out)


def _document(
    record: Mapping[str, Any],
    doc: DocumentMap,
    title: str,
    text: str,
    subject: Doc,
    ident: list[Doc],
    related: str | None,
) -> Doc:
    rev = record.get("review") or {}
    when = _dt(rev.get("at"), record.get("created_at"))
    if doc.resource == "Communication":
        res: Doc = {
            "resourceType": "Communication",
            "identifier": ident,
            "status": "completed",
            "subject": subject,
            "payload": [{"contentString": text}],
        }
        if when:
            res["sent"] = when
        if related:
            res["about"] = [{"reference": related}]
        return res
    res = {
        "resourceType": "DocumentReference",
        "identifier": ident,
        "status": "current",
        "docStatus": "final",
        "subject": subject,
        "description": title,
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain; charset=utf-8",
                    "data": base64.b64encode(text.encode()).decode(),
                    "title": title,
                }
            }
        ],
    }
    if doc.type:
        res["type"] = {"coding": [_coding(doc.type, doc.type_system)]}
    if when:
        res["date"] = when
    if related:
        res["context"] = {"related": [{"reference": related}]}
    return res


# --------------------------------------------------------------------------- bundle


def build_export(
    record: Mapping[str, Any],
    profile: Profile,
    uc: UseCaseMap,
    patient: Mapping[str, Any],
    *,
    call_id: str,
    call: Mapping[str, Any] | None = None,
    transcript: list[Mapping[str, Any]] | None = None,
) -> ExportBundle:
    """Everything the profile says to send for one signed-off call.

    ``patient`` is a FHIR Reference ({"reference": "Patient/<id>"} [+ display]).
    ``transcript`` is ignored unless the transcript document is enabled.
    """
    system = profile.identifiers.system
    answers = answers_from(record, call)
    subject = dict(patient)
    skipped: list[dict[str, str]] = []
    entries: list[Entry] = []
    source_map: list[dict[str, Any]] = []
    tr = _Trace(record, call_id)

    def add(rtype: str, value: str, res: Doc | None) -> str | None:
        rows = tr.rebase(len(entries))
        tr.rows = []
        if res is None:
            return None
        url = full_url(system, value)
        source_map.extend(rows)
        entries.append(Entry(rtype, value, url, res))
        return url

    qr_url = add(
        "QuestionnaireResponse",
        call_id,
        _qr(record, uc, answers, subject, {"system": system, "value": call_id}, skipped, tr),
    )
    add(
        "Task",
        f"{call_id}-task",
        _task(
            record,
            uc,
            subject,
            [{"system": system, "value": f"{call_id}-task"}],
            qr_url,
            tr,
        ),
    )
    for ob in uc.observations:
        value = f"{call_id}-obs-{ob.source}"
        add(
            "Observation",
            value,
            _obs(
                record,
                ob,
                answers,
                subject,
                [{"system": system, "value": value}],
                qr_url,
                skipped,
                tr,
            ),
        )
    if uc.summary.enabled:
        value = f"{call_id}-summary"
        _doc_trace(tr, uc.summary.resource, call_id)
        for part in ("encounter", "disposition", "review"):
            tr.when(_doc_text_target(uc.summary.resource), part, note="summary text")
        add(
            uc.summary.resource,
            value,
            _document(
                record,
                uc.summary,
                "Outreach call summary",
                summary_text(record),
                subject,
                [{"system": system, "value": value}],
                qr_url,
            ),
        )
    warnings: list[str] = []
    if uc.transcript.enabled:
        text = transcript_text(list(transcript or []), uc.transcript.language)
        if text:
            value = f"{call_id}-transcript"
            _doc_trace(tr, uc.transcript.resource, call_id)
            tr.add(
                None,
                _doc_text_target(uc.transcript.resource),
                note=f"transcript ({uc.transcript.language}) from transcripts/{call_id}",
            )
            add(
                uc.transcript.resource,
                value,
                _document(
                    record,
                    uc.transcript,
                    "Outreach call transcript",
                    text,
                    subject,
                    [{"system": system, "value": value}],
                    qr_url,
                ),
            )
        else:
            warnings.append("transcript enabled but the call has no transcript")
    bundle: Doc = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": e.full_url,
                "resource": e.resource,
                "request": _request(profile, e.resource_type, system, e.ident),
            }
            for e in entries
        ],
    }
    return ExportBundle(
        entries=entries,
        bundle=bundle,
        skipped=skipped,
        warnings=warnings,
        source_map=source_map,
    )


def _doc_text_target(resource: str) -> str:
    if resource == "Communication":
        return "/payload/0/contentString"
    return "/content/0/attachment/data"


def _doc_trace(tr: _Trace, resource: str, call_id: str) -> None:
    tr.identifier("/identifier/0/value")
    tr.subject("/subject")
    if resource == "Communication":
        tr.dt("/sent", ("review", "at"), ("created_at",))
    else:
        tr.dt("/date", ("review", "at"), ("created_at",))


def patient_reference(
    patient_id: str, record: Mapping[str, Any], profile: Profile
) -> dict[str, str]:
    ref = {"reference": f"Patient/{patient_id}"}
    name = (record.get("patient") or {}).get("name")
    if profile.patient.include_display_name and name:
        ref["display"] = str(name)
    return ref
