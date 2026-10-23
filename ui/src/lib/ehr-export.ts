/**
 * Admin → Integrations → EHR export (docs/EHR-EXPORT-PLAN.md §4): the API
 * contract (§4.2b) as types, and pure helpers for the mapper and the preview.
 *
 * The editor works on the profile exactly as the API sends it (JSON, the same
 * shape as the YAML; packages/voa_ehr/src/voa_ehr/profile.py). Nothing is
 * translated into a UI model: every edit is a path update on that JSON, so
 * fields the UI does not show survive a round trip untouched
 * (`advancedFields` lists them for the "edit in YAML" note).
 */

import { Pointer, dottedToPointer, formatPointer, isSelfOrAncestor, parsePointer, resolvePointer } from "./json-pointer";

// --------------------------------------------------------------------------- contract

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type JsonObject = { [k: string]: Json };
/** The export profile as JSON (profile.py `Profile`). */
export type EhrProfile = JsonObject;

export const ANSWER_TYPES = ["boolean", "integer", "decimal", "string", "coding", "date", "quantity"] as const;
export type AnswerType = (typeof ANSWER_TYPES)[number];

export interface EhrProblem {
  level: "error" | "warning";
  path: string;
  message: string;
}

export type VersionStatus = "draft" | "active" | "retired";

export interface EhrVersion {
  version: number;
  status: VersionStatus;
  /** Left out in the versions list. */
  profile?: EhrProfile;
  problems?: EhrProblem[];
  saved_by: string;
  saved_at: string;
  activated_by?: string | null;
  activated_at?: string | null;
  note?: string | null;
}

/** GET /api/ehr-export */
export interface EhrExportState {
  active_version: number | null;
  draft_version: number | null;
  active: EhrVersion | null;
  draft: EhrVersion | null;
  /** The use-case spec ids a mapping may name (for "Add use case"). */
  use_cases?: string[];
}

export interface EhrQuestion {
  id: string;
  ask: string;
  /** The question kind (yes_no, scale, choice, text, text_parsed, ...), or an engine field's. */
  type: string;
  choices: string[];
  allowed_types: AnswerType[];
  parser?: string | null;
  /** An engine field (patient_questions, blood_sugar_reading), not a spec question. */
  engine?: boolean;
}

/** GET /api/ehr-export/questions?campaign_id= */
export interface EhrQuestionsResponse {
  campaign_id: string;
  spec_version: number;
  questions: EhrQuestion[];
}

export type PreviewSource = { sample: string } | { call_id: string };

/** GET /api/ehr-export/preview-sources */
export interface EhrPreviewSources {
  samples: { campaign_id: string; label: string }[];
  calls: { call_id: string; campaign_id: string; signed_off_at: string; status: string; label: string }[];
}

/**
 * One link from the app's output to the FHIR it became. Both are JSON
 * Pointers; `source` is null for a value from outside the outbox record (the
 * transcript, or answers read from the call doc on old records).
 */
export interface SourceLink {
  source: Pointer | null;
  target: Pointer;
  question_id?: string;
  note?: string;
}

export interface SkippedAnswer {
  question_id: string;
  reason: string;
  /** A pointer into source_record (null when the answer is not in it). */
  source: Pointer | null;
}

export interface PreviewError {
  path?: string;
  message: string;
}

/** POST /api/ehr-export/preview */
export interface EhrPreview {
  ok: boolean;
  source_record: Json;
  bundle: Json;
  source_map: SourceLink[];
  skipped: SkippedAnswer[];
  warnings: string[];
  errors: PreviewError[];
  /** Which profile the API used: body | draft | active | example. */
  profile_source?: string;
}

export interface SimRequest {
  method: string;
  url: string;
  status: number;
}

/** POST /api/ehr-export/simulate */
export interface SimRun {
  requests: SimRequest[];
  created: number;
  updated: number;
  created_refs?: string[];
  updated_refs?: string[];
  outcome?: { status: string | null; error: string | null; refs: string[] };
}

export interface EhrSimulation {
  runs: SimRun[];
  resources: Json[];
}

export interface OutboxRecord {
  call_id: string;
  /** First name + last initial. */
  label?: string;
  status: string;
  created_at: string;
  exported_at?: string | null;
  last_error?: string | null;
  attempts?: number | null;
}

export const OUTBOX_STATUSES = ["pending_export", "exported", "failed", "skipped", "withdrawn", "withdrawn_done"] as const;

/** Requeue is offered on these (plan §3.1). */
export function canRequeue(status: string): boolean {
  return status === "failed" || status === "skipped";
}

// --------------------------------------------------------------------------- normalising

function asObj(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/**
 * A preview response in the §4.2b shape. Tolerates the engine's own shape too
 * (skipped `{question, reason}`, errors as strings, no source_map), so the
 * screen works while the API is being finished.
 */
export function normalizePreview(raw: unknown): EhrPreview {
  const r = asObj(raw);
  const record = (r.source_record ?? null) as Json;
  const items = (asObj(asObj(record).questionnaire_response).item ?? []) as unknown[];
  const sourceOf = (qid: string): Pointer | null => {
    const i = Array.isArray(items) ? items.findIndex((it) => asObj(it).linkId === qid) : -1;
    return i >= 0 ? `/questionnaire_response/item/${i}/answer` : null;
  };
  const skipped = (Array.isArray(r.skipped) ? r.skipped : []).map((s) => {
    const o = asObj(s);
    const qid = String(o.question_id ?? o.question ?? "");
    const source = typeof o.source === "string" ? o.source : o.source === null ? null : sourceOf(qid);
    return { question_id: qid, reason: String(o.reason ?? ""), source };
  });
  const errors = (Array.isArray(r.errors) ? r.errors : []).map((e) =>
    typeof e === "string" ? { message: e } : { message: String(asObj(e).message ?? ""), ...(asObj(e).path ? { path: String(asObj(e).path) } : {}) },
  );
  const source_map = (Array.isArray(r.source_map) ? r.source_map : [])
    .map((l) => asObj(l))
    .filter((l) => (typeof l.source === "string" || l.source === null) && typeof l.target === "string")
    .map((l) => ({
      source: l.source as string | null,
      target: l.target as string,
      ...(l.question_id ? { question_id: String(l.question_id) } : {}),
      ...(l.note ? { note: String(l.note) } : {}),
    }));
  return {
    ok: typeof r.ok === "boolean" ? r.ok : errors.length === 0,
    source_record: record,
    bundle: (r.bundle ?? null) as Json,
    source_map,
    skipped,
    warnings: (Array.isArray(r.warnings) ? r.warnings : []).map(String),
    errors,
    ...(typeof r.profile_source === "string" ? { profile_source: r.profile_source } : {}),
  };
}

// --------------------------------------------------------------------------- reading the profile

export type Path = ReadonlyArray<string | number>;

export function getIn(obj: unknown, path: Path): unknown {
  let cur = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string | number, unknown>)[k];
  }
  return cur;
}

export function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : v === undefined || v === null ? fallback : String(v);
}

export function bool(v: unknown, fallback: boolean): boolean {
  return typeof v === "boolean" ? v : fallback;
}

export function useCases(p: EhrProfile | null): JsonObject[] {
  const u = p?.use_cases;
  return Array.isArray(u) ? (u.filter((x) => x && typeof x === "object" && !Array.isArray(x)) as JsonObject[]) : [];
}

export function itemOf(p: EhrProfile, uc: number, qid: string): JsonObject | null {
  const it = getIn(p, ["use_cases", uc, "questionnaire_response", "items", qid]);
  return it && typeof it === "object" && !Array.isArray(it) ? (it as JsonObject) : null;
}

export function excludedOf(p: EhrProfile, uc: number): string[] {
  const e = getIn(p, ["use_cases", uc, "questionnaire_response", "exclude"]);
  return Array.isArray(e) ? e.map(String) : [];
}

// --------------------------------------------------------------------------- editing (pure reducers)

/**
 * `obj` with `value` at `path`, copying only the containers on the way;
 * `undefined` removes the key (or array element). Missing containers are
 * created (an object, or an array when the next key is a number).
 */
export function setIn<T>(obj: T, path: Path, value: unknown): T {
  if (path.length === 0) return value as T;
  const [k, ...rest] = path;
  const base: unknown = obj ?? (typeof k === "number" ? [] : {});
  if (Array.isArray(base)) {
    const copy = base.slice();
    const i = Number(k);
    if (rest.length === 0 && value === undefined) copy.splice(i, 1);
    else copy[i] = setIn(copy[i], rest, value);
    return copy as T;
  }
  const o = { ...(base as Record<string, unknown>) };
  if (rest.length === 0 && value === undefined) delete o[k as string];
  else o[k as string] = setIn(o[k as string], rest, value);
  return o as T;
}

const qrPath = (uc: number): Path => ["use_cases", uc, "questionnaire_response"];
const itemPath = (uc: number, qid: string): Path => [...qrPath(uc), "items", qid];

/** `obj` with `key` inserted so keys follow `order` (spec order); unknown keys keep their place. */
export function insertOrdered(obj: JsonObject, key: string, value: Json, order: string[]): JsonObject {
  if (key in obj) return { ...obj, [key]: value };
  const rank = (k: string) => {
    const i = order.indexOf(k);
    return i < 0 ? Number.MAX_SAFE_INTEGER : i;
  };
  const keys = Object.keys(obj);
  let at = keys.length;
  for (let i = 0; i < keys.length; i++) {
    if (rank(keys[i]) > rank(key)) {
      at = i;
      break;
    }
  }
  const out: JsonObject = {};
  keys.forEach((k, i) => {
    if (i === at) out[key] = value;
    out[k] = obj[k];
  });
  if (at === keys.length) out[key] = value;
  return out;
}

/**
 * Set a question's EHR linkId. A question with no mapping yet gets one
 * (type string, which every question allows), placed in spec order.
 */
export function setLinkId(p: EhrProfile, uc: number, qid: string, linkId: string, order: string[] = []): EhrProfile {
  const item = itemOf(p, uc, qid);
  if (item) return setIn(p, [...itemPath(uc, qid), "link_id"], linkId);
  let out = p;
  if (!getIn(p, qrPath(uc))) out = setIn(out, qrPath(uc), { questionnaire: "", items: {} });
  const items = (getIn(out, [...qrPath(uc), "items"]) as JsonObject | undefined) ?? {};
  return setIn(out, [...qrPath(uc), "items"], insertOrdered(items, qid, { link_id: linkId, type: "string" }, order));
}

/** Stop mapping a question (its answer then follows `unmapped`). */
export function unmapItem(p: EhrProfile, uc: number, qid: string): EhrProfile {
  return setIn(p, itemPath(uc, qid), undefined);
}

/** Keys only some answer types may carry (profile.py ValueSpec._shape). */
const CODING_KEYS = ["system", "codes"] as const;
const QUANTITY_KEYS = ["unit", "unit_system", "unit_code"] as const;

/**
 * Change an answer type at `base` (an item or an Observation). Keys the new
 * type may not carry are removed, as the model would refuse them.
 */
export function setValueType(p: EhrProfile, base: Path, type: AnswerType): EhrProfile {
  let out = setIn(p, [...base, "type"], type);
  if (type !== "coding") for (const k of CODING_KEYS) out = setIn(out, [...base, k], undefined);
  if (type !== "quantity") for (const k of QUANTITY_KEYS) out = setIn(out, [...base, k], undefined);
  if (type === "coding" && getIn(out, [...base, "codes"]) === undefined) out = setIn(out, [...base, "codes"], {});
  return out;
}

export function setItemType(p: EhrProfile, uc: number, qid: string, type: AnswerType): EhrProfile {
  return setValueType(p, itemPath(uc, qid), type);
}

/**
 * One cell of a code map at `base`: the code, display or per-code system for
 * an app answer. An entry left with nothing in it is removed (the answer then
 * shows as "no code").
 */
export function setCodeAt(p: EhrProfile, base: Path, answer: string, field: "code" | "display" | "system", value: string): EhrProfile {
  const cur = (getIn(p, [...base, "codes", answer]) as JsonObject | undefined) ?? {};
  const next: JsonObject = { ...cur, [field]: value };
  if (!value) delete next[field];
  if (field !== "code" && next.code === undefined) next.code = "";
  const empty = !str(next.code) && !str(next.display) && !str(next.system);
  return setIn(p, [...base, "codes", answer], empty ? undefined : next);
}

export function setCode(p: EhrProfile, uc: number, qid: string, answer: string, field: "code" | "display" | "system", value: string): EhrProfile {
  return setCodeAt(p, itemPath(uc, qid), answer, field, value);
}

/** The Send toggle: off puts the question in `exclude` (never sent, mapped or not). */
export function toggleSend(p: EhrProfile, uc: number, qid: string, send: boolean): EhrProfile {
  let out = p;
  if (!getIn(p, qrPath(uc))) out = setIn(out, qrPath(uc), { questionnaire: "", items: {} });
  const ex = excludedOf(out, uc).filter((q) => q !== qid);
  if (!send) ex.push(qid);
  return setIn(out, [...qrPath(uc), "exclude"], ex);
}

/** Turn the QuestionnaireResponse or the Task on or off (creating the block when needed). */
export function setResourceEnabled(p: EhrProfile, uc: number, which: "questionnaire_response" | "task", enabled: boolean): EhrProfile {
  const cur = getIn(p, ["use_cases", uc, which]);
  if (!cur) {
    if (!enabled) return p;
    return setIn(p, ["use_cases", uc, which], which === "task" ? { enabled: true } : { enabled: true, questionnaire: "", items: {} });
  }
  return setIn(p, ["use_cases", uc, which, "enabled"], enabled);
}

/**
 * Summary / transcript. Turning one on needs the privacy acknowledgement
 * (`acknowledged`); without it nothing changes. Turning it off keeps the
 * acknowledgement as it was.
 */
export function setDocumentEnabled(p: EhrProfile, uc: number, which: "summary" | "transcript", enabled: boolean, acknowledged: boolean): EhrProfile {
  if (enabled && !acknowledged) return p;
  let out = setIn(p, ["use_cases", uc, which, "enabled"], enabled);
  if (enabled) out = setIn(out, ["use_cases", uc, which, "phi_acknowledged"], true);
  return out;
}

export function addObservation(p: EhrProfile, uc: number, source: string): EhrProfile {
  const obs = (getIn(p, ["use_cases", uc, "observations"]) as Json[] | undefined) ?? [];
  const row: JsonObject = { source, code: { system: "http://loinc.org", code: "", display: "" }, category: "survey", type: "string" };
  return setIn(p, ["use_cases", uc, "observations"], [...obs, row]);
}

export function removeObservation(p: EhrProfile, uc: number, index: number): EhrProfile {
  return setIn(p, ["use_cases", uc, "observations", index], undefined);
}

export function addUseCase(p: EhrProfile, campaignId: string): EhrProfile {
  const list = useCases(p);
  return setIn(p, ["use_cases"], [
    ...list,
    { campaign_id: campaignId, spec_versions: [], questionnaire_response: { enabled: true, questionnaire: "", unmapped: "skip", exclude: [], items: {} } },
  ]);
}

export function removeUseCase(p: EhrProfile, uc: number): EhrProfile {
  return setIn(p, ["use_cases", uc], undefined);
}

// --------------------------------------------------------------------------- code systems

export const CODE_SYSTEM_PRESETS: { key: string; label: string; system: string }[] = [
  { key: "loinc", label: "LOINC", system: "http://loinc.org" },
  { key: "snomed", label: "SNOMED CT", system: "http://snomed.info/sct" },
];

/** "loinc" | "snomed" | "custom" for a code system URL. */
export function presetOf(system: string): string {
  return CODE_SYSTEM_PRESETS.find((c) => c.system === system)?.key ?? "custom";
}

export const TIERS = ["emergency", "urgent_same_day", "routine_next_business_day", "informational", "none"] as const;
export const TIER_LABEL: Record<string, string> = {
  emergency: "Emergency",
  urgent_same_day: "Urgent, same day",
  routine_next_business_day: "Routine, next business day",
  informational: "Informational",
  none: "None",
};
export const DEFAULT_PRIORITY: Record<string, string> = {
  emergency: "stat",
  urgent_same_day: "urgent",
  routine_next_business_day: "routine",
  informational: "routine",
  none: "routine",
};
export const TASK_PRIORITIES = ["routine", "urgent", "asap", "stat"] as const;
export const TASK_STATUSES = ["draft", "requested", "received", "accepted", "rejected", "ready", "cancelled", "in-progress", "on-hold", "failed", "completed"] as const;

// --------------------------------------------------------------------------- questions table helpers

/** The answer types the dropdown offers: the question's, widened by a value_map (profile.py `_check_value`), plus the current one. */
export function typeOptions(q: EhrQuestion | undefined, item: JsonObject | null): AnswerType[] {
  const allowed = new Set<string>(q?.allowed_types?.length ? q.allowed_types : ["string"]);
  const vm = item?.value_map;
  if (vm && typeof vm === "object" && Object.keys(vm).length) for (const t of ["boolean", "integer", "decimal", "coding", "string"]) allowed.add(t);
  if (item && typeof item.type === "string") allowed.add(item.type);
  return ANSWER_TYPES.filter((t) => allowed.has(t));
}

/** The app answers a coding item has no code for (after its value_map), like profile_problems' warning. */
export function missingCodes(q: EhrQuestion | undefined, spec: JsonObject | null): string[] {
  if (!q || !spec || spec.type !== "coding" || (spec.constant !== undefined && spec.constant !== null)) return [];
  const codes = (spec.codes && typeof spec.codes === "object" ? spec.codes : {}) as Record<string, Json>;
  const vm = (spec.value_map && typeof spec.value_map === "object" ? spec.value_map : {}) as Record<string, Json>;
  return q.choices.filter((c) => {
    const key = String(vm[c] ?? c);
    const entry = codes[key] as JsonObject | undefined;
    return !entry || !str(entry.code);
  });
}

/** The rows of a code-map grid: the question's choices, then any other mapped keys. */
export function codeMapRows(q: EhrQuestion | undefined, spec: JsonObject | null): string[] {
  const codes = Object.keys((spec?.codes && typeof spec.codes === "object" ? spec.codes : {}) as object);
  const choices = q?.choices ?? [];
  return [...choices, ...codes.filter((c) => !choices.includes(c))];
}

/** Problems whose path is `prefix` or under it (both API path styles). */
export function problemsUnder(problems: EhrProblem[], prefix: Pointer): EhrProblem[] {
  return problems.filter((pr) => {
    const p = dottedToPointer(pr.path);
    return isSelfOrAncestor(prefix, p) && (prefix !== "" || p === "");
  });
}

export function hasErrors(problems: EhrProblem[] | null | undefined): boolean {
  return !!problems?.some((p) => p.level === "error");
}

/** Value-spec keys the table edits; anything else on an item is "advanced". */
const ITEM_KEYS = new Set(["link_id", "text", "type", "system", "codes", "unit", "unit_system", "unit_code"]);
const OBS_KEYS = new Set(["source", "code", "code_system", "category", "type", "system", "codes", "unit", "unit_system", "unit_code"]);
const DOC_KEYS = new Set(["enabled", "resource", "phi_acknowledged", "language"]);
const TASK_KEYS = new Set(["enabled", "emit_when", "status", "intent", "priority", "code", "code_system", "description", "include_nurse_note", "include_reason", "owner"]);
const QR_KEYS = new Set(["enabled", "questionnaire", "unmapped", "unmapped_prefix", "exclude", "author_display", "items"]);
const UC_KEYS = new Set(["campaign_id", "spec_versions", "questionnaire_response", "task", "observations", "summary", "transcript"]);
const TOP_KEYS = new Set(["profile_schema", "id", "name", "version", "description", "target", "patient", "identifiers", "use_cases", "validate_output"]);
const TARGET_KEYS = new Set(["fhir_version", "base_url", "auth", "timeout_s", "submit", "write_mode"]);
const AUTH_KEYS = new Set(["type", "secret", "client_id", "token_url", "scope"]);
const PATIENT_KEYS = new Set(["identifier_system", "not_found", "search_with_birthdate", "include_display_name"]);

function isDefault(key: string, v: Json): boolean {
  if (key === "date_formats") return Array.isArray(v) && v.length === 1 && v[0] === "%Y-%m-%d";
  if (key === "separator") return v === "; ";
  if (key === "value_map" || key === "codes") return !!v && typeof v === "object" && Object.keys(v).length === 0;
  if (key === "concat") return Array.isArray(v) && v.length === 0;
  if (key === "constant" || key === "type" || key === "type_system") return v === null || v === "";
  return false;
}

function extraKeys(obj: unknown, known: Set<string>, prefix: string, out: string[]) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return;
  for (const [k, v] of Object.entries(obj as JsonObject)) if (!known.has(k) && !isDefault(k, v)) out.push(`${prefix}${k}`);
}

/**
 * Fields present in the profile that the mapper does not edit (value_map,
 * constant, concat, date_formats, document types, unknown keys). They are
 * kept as they are; the UI lists them so the admin knows to use the YAML.
 */
export function advancedFields(p: EhrProfile | null): string[] {
  if (!p) return [];
  const out: string[] = [];
  extraKeys(p, TOP_KEYS, "", out);
  extraKeys(p.target, TARGET_KEYS, "target.", out);
  extraKeys(getIn(p, ["target", "auth"]), AUTH_KEYS, "target.auth.", out);
  extraKeys(p.patient, PATIENT_KEYS, "patient.", out);
  useCases(p).forEach((u, i) => {
    const b = `use_cases[${i}].`;
    extraKeys(u, UC_KEYS, b, out);
    const qr = u.questionnaire_response as JsonObject | undefined;
    extraKeys(qr, QR_KEYS, `${b}questionnaire_response.`, out);
    for (const [qid, it] of Object.entries((qr?.items ?? {}) as JsonObject)) extraKeys(it, ITEM_KEYS, `${b}questionnaire_response.items.${qid}.`, out);
    extraKeys(u.task, TASK_KEYS, `${b}task.`, out);
    (Array.isArray(u.observations) ? u.observations : []).forEach((o, j) => extraKeys(o, OBS_KEYS, `${b}observations[${j}].`, out));
    for (const d of ["summary", "transcript"]) extraKeys(u[d], DOC_KEYS, `${b}${d}.`, out);
  });
  return out;
}

/** Advanced fields of one item, by key ("value_map", "constant", ...). */
export function advancedItemKeys(item: JsonObject | null): string[] {
  const out: string[] = [];
  extraKeys(item, ITEM_KEYS, "", out);
  return out;
}

// --------------------------------------------------------------------------- traceability

export type Side = "source" | "target";

/**
 * The counterpart nodes of a clicked node. For a node at `p` on `side`:
 * 1. links whose own end is exactly `p`;
 * 2. else the links of its nearest ancestor that has any (a leaf inside a
 *    mapped answer, e.g. valueCoding/code);
 * 3. else every link inside `p` (a container: an item, a resource, an entry).
 * Returns the matched links and the other side's pointers, de-duplicated.
 */
export function counterparts(all: SourceLink[], side: Side, p: Pointer): { links: SourceLink[]; pointers: Pointer[]; own: Pointer[] } {
  const other: Side = side === "source" ? "target" : "source";
  // A link with no source (the transcript, the call doc) cannot be traced to the left.
  const map = all.filter((l) => l.source !== null) as (SourceLink & { source: Pointer })[];
  let links = map.filter((l) => l[side] === p);
  if (!links.length) {
    let best = -1;
    for (const l of map) {
      const end = l[side];
      if (end !== p && isSelfOrAncestor(end, p)) {
        const depth = end === "" ? 0 : parsePointer(end).length;
        if (depth > best) {
          best = depth;
          links = [l];
        } else if (depth === best) links.push(l);
      }
    }
  }
  if (!links.length) links = map.filter((l) => isSelfOrAncestor(p, l[side]));
  const uniq = (xs: Pointer[]) => [...new Set(xs)];
  return { links, pointers: uniq(links.map((l) => l[other])), own: uniq(links.map((l) => l[side])) };
}

/** "← pain_trend" badges for mapped target nodes: target pointer -> label. */
export function targetBadges(map: SourceLink[]): Map<Pointer, string> {
  const out = new Map<Pointer, string>();
  for (const l of map) {
    const label = l.question_id ?? (l.source === null ? (l.note ?? "outside the record") : sourceLabel(l.source));
    const prev = out.get(l.target);
    if (!prev) out.set(l.target, label);
    else if (!prev.split(", ").includes(label)) out.set(l.target, `${prev}, ${label}`);
  }
  return out;
}

/** "/disposition/follow_up" -> "follow_up"; "/questionnaire_response/item/4/answer" -> "item 4". */
export function sourceLabel(src: Pointer): string {
  const t = parsePointer(src);
  const i = t.indexOf("item");
  if (i >= 0 && t[i + 1] !== undefined) return `item ${t[i + 1]}`;
  return t[t.length - 1] ?? "record";
}

/** Skipped answers on the left: source pointer -> reason. */
export function skippedBySource(skipped: SkippedAnswer[]): Map<Pointer, string> {
  const out = new Map<Pointer, string>();
  for (const s of skipped) if (s.source) out.set(s.source, out.has(s.source) ? `${out.get(s.source)}; ${s.reason}` : s.reason);
  return out;
}

/** FHIR errors with a path, on the right: bundle pointer -> messages. Paths may be pointers or dotted. */
export function errorsByTarget(errors: PreviewError[], bundle: Json): Map<Pointer, string> {
  const out = new Map<Pointer, string>();
  for (const e of errors) {
    if (!e.path) continue;
    let p = dottedToPointer(e.path);
    // Walk up to a node that exists, so the flag lands somewhere visible.
    while (p !== "" && resolvePointer(bundle, p) === undefined) p = formatPointer(parsePointer(p).slice(0, -1));
    out.set(p, out.has(p) ? `${out.get(p)}; ${e.message}` : e.message);
  }
  return out;
}

/** The record's answer items keyed by question id (for the questions table's "sample answer"). */
export function sampleAnswers(record: Json): Record<string, Json> {
  const items = getIn(record, ["questionnaire_response", "item"]);
  const out: Record<string, Json> = {};
  if (Array.isArray(items)) for (const it of items) if (it && typeof it === "object" && !Array.isArray(it) && typeof it.linkId === "string") out[it.linkId] = it.answer ?? null;
  return out;
}

// --------------------------------------------------------------------------- diff (Activate)

export interface DiffEntry {
  path: Pointer;
  kind: "added" | "removed" | "changed";
  before?: Json;
  after?: Json;
}

function isObj(v: unknown): v is JsonObject {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** What changed from `a` to `b`, leaf by leaf (arrays by index). Equal documents give []. */
export function jsonDiff(a: Json | undefined, b: Json | undefined, path: Pointer = ""): DiffEntry[] {
  if (a === undefined && b === undefined) return [];
  if (a === undefined) return [{ path, kind: "added", after: b }];
  if (b === undefined) return [{ path, kind: "removed", before: a }];
  if (isObj(a) && isObj(b)) {
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
    return keys.flatMap((k) => jsonDiff(a[k], b[k], path + formatPointer([k])));
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    const n = Math.max(a.length, b.length);
    const out: DiffEntry[] = [];
    for (let i = 0; i < n; i++) out.push(...jsonDiff(a[i], b[i], path + formatPointer([i])));
    return out;
  }
  return JSON.stringify(a) === JSON.stringify(b) ? [] : [{ path, kind: "changed", before: a, after: b }];
}

/** "/use_cases/0/questionnaire_response/items/pain_trend/codes/same/code" -> "use_cases[0].questionnaire_response.items.pain_trend.codes.same.code" */
export function readablePath(p: Pointer): string {
  if (p === "") return "(whole profile)";
  return parsePointer(p)
    .map((t, i) => (/^\d+$/.test(t) ? `[${t}]` : i === 0 ? t : `.${t}`))
    .join("");
}

export function shortJson(v: Json | undefined, max = 80): string {
  if (v === undefined) return "";
  const s = JSON.stringify(v);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

// --------------------------------------------------------------------------- misc

/** A file's text as a draft upload body: JSON is sent as a profile, anything else as YAML text. */
export function uploadBody(text: string): { profile: EhrProfile } | { yaml: string } {
  const t = text.trim();
  if (t.startsWith("{")) {
    try {
      const parsed = JSON.parse(t);
      if (isObj(parsed)) return { profile: parsed as EhrProfile };
    } catch {
      // not JSON after all: let the API parse it as YAML (JSON is YAML)
    }
  }
  return { yaml: text };
}

/** "post_op" -> its name from the campaigns list, else the id. */
export function ucLabel(campaignId: string, campaigns: { id: string; name: string }[] | undefined): string {
  return campaigns?.find((c) => c.id === campaignId)?.name ?? campaignId;
}
