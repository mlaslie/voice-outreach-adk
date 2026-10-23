/**
 * Sample mode (?data=demo) for Admin → EHR export: no API. The example
 * profile and the fictional sample come from ./ehr-export-fixtures.json, and
 * ./ehr-export-sample.json is the real engine's preview of them
 * (scripts/ehr_export_ui_fixtures.py; `voa_ehr.exporter.preview`).
 *
 * So that editing the mapper visibly changes the preview, validate, preview
 * and simulate are re-done here in the browser by a small port of the
 * engine's rules (voa_ehr transform.py and profile.py). It covers the
 * resources and checks the mapper edits; the tests hold it to the engine's
 * output for the example (bundle, source map, problems, simulate). It is a
 * stand-in for the demo only: live data always uses the API.
 */

import fixtures from "./ehr-export-fixtures.json";
import samplePreview from "./ehr-export-sample.json";
import {
  EhrPreview,
  EhrPreviewSources,
  EhrProblem,
  EhrProfile,
  EhrQuestion,
  EhrQuestionsResponse,
  EhrSimulation,
  EhrVersion,
  Json,
  JsonObject,
  OutboxRecord,
  SimRequest,
  SkippedAnswer,
  SourceLink,
  getIn,
  normalizePreview,
  str,
  useCases,
} from "./ehr-export";

export const DEMO_NOTE = "Sample data: nothing is saved";

export const EXAMPLE_PROFILE = fixtures.example_profile as unknown as EhrProfile;
export const SAMPLE = fixtures.sample as unknown as { campaign_id: string; call_id: string; record: JsonObject; call: JsonObject };
export const SAMPLE_PREVIEW: EhrPreview = normalizePreview(samplePreview);
export const SAMPLE_QUESTIONS = fixtures.questions as unknown as Record<string, EhrQuestionsResponse>;
export const EXAMPLE_PROBLEMS = fixtures.example_problems as EhrProblem[];
export const SAMPLE_SIMULATION = fixtures.simulate as unknown as EhrSimulation;

export function demoQuestions(campaignId: string): EhrQuestionsResponse {
  return SAMPLE_QUESTIONS[campaignId] ?? { campaign_id: campaignId, spec_version: 1, questions: [] };
}

export const DEMO_SOURCES: EhrPreviewSources = {
  samples: [{ campaign_id: "post_op", label: "Fictional sample: post-op (Jordan R.)" }],
  calls: [],
};

// --------------------------------------------------------------------------- validate (profile.py, abridged)

const SECRET_REF = /^secret:\/\/[A-Za-z0-9_-]{1,255}(#[A-Za-z0-9_-]{1,64})?$/;
const LINK_ID = /^[A-Za-z0-9._\-/:]{1,255}$/;
const PROFILE_ID = /^[a-z][a-z0-9_.-]{0,63}$/;
const TIERS = new Set(["emergency", "urgent_same_day", "routine_next_business_day", "informational", "none"]);
const PRIORITIES = new Set(["routine", "urgent", "asap", "stat"]);

const o = (v: unknown): JsonObject => (v && typeof v === "object" && !Array.isArray(v) ? (v as JsonObject) : {});
const arr = (v: unknown): Json[] => (Array.isArray(v) ? v : []);

function checkSpec(spec: JsonObject, path: string, out: EhrProblem[]) {
  const t = str(spec.type);
  const codes = o(spec.codes);
  const err = (message: string) => out.push({ level: "error", path, message });
  if (!t) err("type: field required");
  if (t === "coding") {
    if (!Object.keys(codes).length) err("type coding needs codes (app value -> code)");
    const missing = Object.entries(codes).filter(([, c]) => !str(o(c).system) && !str(spec.system)).map(([k]) => k);
    if (missing.length && Object.keys(codes).length) err(`coding needs a system (map-level or per code): ${JSON.stringify(missing)}`);
  } else if (Object.keys(codes).length || str(spec.system)) err("codes/system are only for type coding");
  if (str(spec.unit) && t !== "quantity") err("unit is only for type quantity (use quantity for a value with units)");
  if (t === "quantity" && !str(spec.unit)) err("type quantity needs a unit");
  if (arr(spec.concat).length && t !== "string") err("concat produces text: use type string");
}

function checkValue(questions: EhrQuestion[], campaignId: string, qid: string, spec: JsonObject, path: string, out: EhrProblem[]) {
  const q = questions.find((x) => x.id === qid);
  if (!q) {
    out.push({ level: "error", path, message: `'${qid}' is not a question in ${campaignId}` });
    return;
  }
  const vm = o(spec.value_map);
  const t = str(spec.type);
  if (spec.constant === undefined || spec.constant === null) {
    if (!arr(spec.concat).length) {
      const allowed = new Set<string>(q.allowed_types);
      if (Object.keys(vm).length) for (const x of ["boolean", "integer", "decimal", "coding", "string"]) allowed.add(x);
      if (t && !allowed.has(t)) {
        out.push({ level: "error", path, message: `type ${t} does not fit '${qid}' (a ${q.type} question); use one of [${[...allowed].sort().map((x) => `'${x}'`).join(", ")}]` });
        return;
      }
    }
  }
  if (t === "coding" && q.choices.length && (spec.constant === undefined || spec.constant === null)) {
    const codes = o(spec.codes);
    const uncovered = q.choices.filter((c) => !(String(vm[c] ?? c) in codes) || !str(o(codes[String(vm[c] ?? c)]).code));
    if (uncovered.length)
      out.push({ level: "warning", path, message: `no code for answers [${uncovered.map((c) => `'${c}'`).join(", ")}]: left out (QuestionnaireResponse: per 'unmapped')` });
    const mapped = new Set(q.choices.map((c) => String(vm[c] ?? c)));
    const extra = Object.keys(codes).filter((k) => !mapped.has(k)).sort();
    if (extra.length) out.push({ level: "warning", path, message: `codes [${extra.map((c) => `'${c}'`).join(", ")}] match no answer of '${qid}'` });
  }
  if (t === "boolean" && q.type === "yes_no_unknown" && !("unknown" in vm))
    out.push({
      level: "warning",
      path,
      message: `'${qid}' can be 'unknown', which is not a boolean: such an answer is left out (in a QuestionnaireResponse, handled per 'unmapped'); a coding keeps it`,
    });
}

/** profile_problems plus the model checks the mapper can break, as the API reports them. */
export function demoValidate(p: EhrProfile, questionsFor: (campaignId: string) => EhrQuestionsResponse = demoQuestions): EhrProblem[] {
  const out: EhrProblem[] = [];
  const err = (path: string, message: string) => out.push({ level: "error", path, message });
  if (!PROFILE_ID.test(str(p.id))) err("id", "id: lower case letters, digits, . _ - (start with a letter)");
  if (!str(p.name)) err("name", "name: field required");
  const base = str(getIn(p, ["target", "base_url"]));
  try {
    const u = new URL(base);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
    if (!(u.protocol === "https:" || (u.protocol === "http:" && local))) err("target.base_url", "base_url must be https (http only for localhost)");
    else if (u.search || u.hash || u.username || u.password) err("target.base_url", "base_url must be a plain https URL with no query or credentials");
  } catch {
    err("target.base_url", "base_url must be https (http only for localhost)");
  }
  const auth = o(getIn(p, ["target", "auth"]));
  const authType = str(auth.type, "none");
  if (str(auth.secret) && !SECRET_REF.test(str(auth.secret))) err("target.auth.secret", "must be a Secret Manager reference: secret://<name>[#version]");
  if (authType !== "none" && !str(auth.secret)) err("target.auth", `auth type ${authType} needs a secret reference`);
  if ((authType === "client_credentials" || authType === "smart_backend") && !(str(auth.client_id) && str(auth.token_url)))
    err("target.auth", `auth type ${authType} needs client_id and token_url`);
  if (!str(getIn(p, ["patient", "identifier_system"]))) err("patient.identifier_system", "identifier_system: field required");
  if (!str(getIn(p, ["identifiers", "system"]))) err("identifiers.system", "system: field required");

  const ucs = useCases(p);
  if (!ucs.length) err("use_cases", "no use case is mapped");
  const seenUc: Record<string, Set<number>[]> = {};
  ucs.forEach((uc, i) => {
    const b = `use_cases[${i}]`;
    const cid = str(uc.campaign_id);
    const versions = new Set(arr(uc.spec_versions).map(Number));
    for (const prev of seenUc[cid] ?? [])
      if ((!prev.size && !versions.size) || [...versions].some((v) => prev.has(v))) err("use_cases", `two mappings for ${cid} cover the same spec versions`);
    (seenUc[cid] ??= []).push(versions);
    const qs = questionsFor(cid);
    if (!qs.questions.length) {
      err(`${b}.campaign_id`, `unknown use case: no campaign '${cid}'`);
      return;
    }
    if (versions.size && !versions.has(qs.spec_version))
      out.push({ level: "warning", path: `${b}.spec_versions`, message: `the current ${cid} spec is version ${qs.spec_version}, not in [${[...versions].join(", ")}]: question ids are checked against it anyway` });
    const qr = uc.questionnaire_response as JsonObject | null | undefined;
    if (qr) {
      const items = o(qr.items);
      const exclude = arr(qr.exclude).map(String);
      const seen = new Map<string, string>();
      if (!str(qr.questionnaire)) err(`${b}.questionnaire_response.questionnaire`, "questionnaire: the EHR Questionnaire's canonical URL is required");
      for (const [qid, raw] of Object.entries(items)) {
        const m = o(raw);
        const path = `${b}.questionnaire_response.items.${qid}`;
        const lid = str(m.link_id);
        if (!LINK_ID.test(lid)) err(path, "link_id: letters, digits and . _ - / : only");
        else if (seen.has(lid)) err(`${b}.questionnaire_response`, `link_id '${lid}' used by ${seen.get(lid)} and ${qid}`);
        else seen.set(lid, qid);
        checkSpec(m, path, out);
        checkValue(qs.questions, cid, qid, m, path, out);
      }
      for (const qid of exclude) {
        if (!qs.questions.some((q) => q.id === qid)) err(`${b}.questionnaire_response.exclude`, `'${qid}' is not a question in ${cid}`);
        if (qid in items) out.push({ level: "warning", path: `${b}.questionnaire_response.exclude`, message: `'${qid}' is both mapped and excluded: it is not sent` });
      }
      if (qr.enabled !== false) {
        const unmapped = qs.questions.filter((q) => !q.engine && !(q.id in items) && !exclude.includes(q.id)).map((q) => `'${q.id}'`);
        if (unmapped.length)
          out.push({ level: "warning", path: `${b}.questionnaire_response.items`, message: `not mapped (${str(qr.unmapped, "skip")}): [${unmapped.join(", ")}]` });
      }
    }
    const task = uc.task as JsonObject | null | undefined;
    if (task) {
      const pr = o(task.priority);
      const bad = Object.keys(pr).filter((t) => !TIERS.has(t)).sort();
      if (bad.length) err(`${b}.task.priority`, `unknown tiers [${bad.join(", ")}]`);
      for (const [t, v] of Object.entries(pr)) if (!PRIORITIES.has(str(v))) err(`${b}.task.priority.${t}`, `priority must be one of routine, urgent, asap, stat`);
      const owner = o(task.owner);
      if (str(owner.mode) === "identifier" && !str(owner.identifier_system)) err(`${b}.task.owner`, "owner mode identifier needs identifier_system");
      if (task.code && !str(o(task.code).system) && !str(task.code_system)) err(`${b}.task`, "task code needs a system");
    }
    const sources = new Set<string>();
    arr(uc.observations).forEach((raw, j) => {
      const ob = o(raw);
      const path = `${b}.observations[${j}]`;
      checkSpec(ob, path, out);
      if (!str(o(ob.code).code)) err(path, "observation code: field required");
      if (!str(o(ob.code).system) && !str(ob.code_system)) err(path, "observation code needs a system");
      checkValue(qs.questions, cid, str(ob.source), ob, path, out);
      if (sources.has(str(ob.source))) err(path, `two observations from '${str(ob.source)}'`);
      sources.add(str(ob.source));
    });
    if (arr(uc.observations).length && (!qr || qr.enabled === false))
      out.push({ level: "warning", path: `${b}.observations`, message: "observations without a QuestionnaireResponse have no derivedFrom link" });
    for (const d of ["summary", "transcript"]) {
      const doc = o(uc[d]);
      if (doc.enabled === true && doc.phi_acknowledged !== true)
        err(`${b}.${d}`, "this sends free-text PHI to the EHR: set phi_acknowledged: true once your privacy office has approved it");
    }
  });
  return out;
}

// --------------------------------------------------------------------------- preview (transform.py, abridged)

const YES = new Set(["yes", "y", "yeah", "yep", "true"]);
function normAnswer(v: unknown): string {
  const s = String(v).trim().toLowerCase();
  if (YES.has(s)) return "yes";
  if (["no", "n", "nope", "false"].includes(s)) return "no";
  return s;
}

const answered = (v: unknown) => v !== null && v !== undefined && (typeof v === "string" ? !!v.trim() : Array.isArray(v) ? v.length > 0 : typeof v === "object" ? Object.keys(v as object).length > 0 : true);
const squash = (v: unknown) => String(v).split(/\s+/).filter(Boolean).join(" ");
const ISO_DT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
const dt = (...c: unknown[]) => c.find((x) => typeof x === "string" && ISO_DT.test(x)) as string | undefined;

function num(v: unknown): number | null {
  if (typeof v === "boolean") return null;
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() && !Number.isNaN(Number(v.trim()))) return Number(v.trim());
  return null;
}

function codingOf(c: JsonObject, defaultSystem: string): JsonObject {
  const out: JsonObject = { system: str(c.system) || defaultSystem, code: str(c.code) };
  if (str(c.display)) out.display = str(c.display);
  return out;
}

/** One value -> {value<Type>: ...}, or null when it cannot be typed (never guessed). */
function typed(spec: JsonObject, v: unknown): JsonObject | null {
  const t = str(spec.type);
  if (t === "boolean") {
    if (typeof v === "boolean") return { valueBoolean: v };
    const k = normAnswer(v);
    return k === "yes" || k === "no" ? { valueBoolean: k === "yes" } : null;
  }
  if (t === "integer" || t === "decimal" || t === "quantity") {
    const n = num(v);
    if (n === null) return null;
    if (t === "integer") return Number.isInteger(n) ? { valueInteger: n } : null;
    if (t === "decimal") return { valueDecimal: n };
    return { valueQuantity: { value: n, unit: str(spec.unit), system: str(spec.unit_system, "http://unitsofmeasure.org"), code: str(spec.unit_code) || str(spec.unit) } };
  }
  if (t === "string") {
    const s = squash(v);
    return s ? { valueString: s } : null;
  }
  if (t === "coding") {
    const c = o(spec.codes)[normAnswer(v)];
    return c && str(o(c).code) ? { valueCoding: codingOf(o(c), str(spec.system)) } : null;
  }
  if (t === "date") {
    const s = String(v).trim();
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? { valueDate: s } : null;
  }
  return null;
}

type Src = string | null;
interface Traced {
  value: JsonObject;
  srcs: Src[];
}

/** values_for with each value's source pointers (transform.py _traced_values): [typed, untypeable]. */
function valuesFor(spec: JsonObject, qid: string, answers: Record<string, Json>, srcs: Map<string, string>): [Traced[], { v: unknown; srcs: Src[] }[]] {
  const raw = answers[qid];
  let vals: { v: unknown; srcs: Src[] }[];
  const concat = arr(spec.concat).map(String);
  if (concat.length) {
    const hits = concat.map((c) => [answers[c], srcs.get(c) ?? null] as const).filter(([p]) => answered(p));
    if (!hits.length) return [[], []];
    vals = [{ v: hits.map(([p]) => squash(p)).join(str(spec.separator, "; ")), srcs: hits.map(([, s]) => s) }];
  } else if (!answered(raw)) return [[], []];
  else if (spec.constant !== undefined && spec.constant !== null) vals = [{ v: spec.constant, srcs: [srcs.get(qid) ?? null] }];
  else {
    const base = srcs.get(qid) ?? null;
    const listed = Array.isArray(raw);
    vals = [];
    (listed ? raw : [raw]).forEach((x, k) => {
      let v: unknown = x;
      let src: Src = listed ? (base === null ? null : `${base}/${k}`) : base;
      if (v && typeof v === "object" && !Array.isArray(v)) {
        v = (v as JsonObject).text;
        src = src === null ? null : `${src}/text`;
      }
      if (answered(v)) vals.push({ v, srcs: [src] });
    });
  }
  const vm = o(spec.value_map);
  const good: Traced[] = [];
  const bad: { v: unknown; srcs: Src[] }[] = [];
  for (const { v, srcs: s } of vals) {
    const mapped = Object.keys(vm).length && !concat.length ? (vm[normAnswer(v)] ?? v) : v;
    const t = typed(spec, mapped);
    if (t) good.push({ value: t, srcs: s });
    else bad.push({ v: mapped, srcs: s });
  }
  return [good, bad];
}

/** urllib.parse.quote(s, safe=":/"). */
export function quote(s: string): string {
  return Array.from(new TextEncoder().encode(s))
    .map((b) => {
      const c = String.fromCharCode(b);
      return /[A-Za-z0-9_.\-~:/]/.test(c) ? c : `%${b.toString(16).toUpperCase().padStart(2, "0")}`;
    })
    .join("");
}

const KNOWN_URLS = new Map<string, string>();
for (const e of arr(getIn(SAMPLE_PREVIEW.bundle, ["entry"]))) {
  const id = o(arr(getIn(e, ["resource", "identifier"]))[0] ?? getIn(e, ["resource", "identifier"]));
  if (id.system && id.value) KNOWN_URLS.set(`${id.system}|${id.value}`, str(o(e).fullUrl));
}

/** The engine's uuid5 fullUrl for known identifiers; a stable stand-in for new ones (sample mode only). */
function fullUrl(system: string, value: string): string {
  const known = KNOWN_URLS.get(`${system}|${value}`);
  if (known) return known;
  let h1 = 0x811c9dc5, h2 = 0x01000193;
  for (const ch of `${system}|${value}`) {
    h1 = Math.imul(h1 ^ ch.charCodeAt(0), 16777619) >>> 0;
    h2 = Math.imul(h2 ^ ch.charCodeAt(0), 2246822519) >>> 0;
  }
  const hex = (h1.toString(16).padStart(8, "0") + h2.toString(16).padStart(8, "0")).repeat(2);
  return `urn:uuid:${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

const OBS_VALUE: Record<string, string> = {
  valueBoolean: "valueBoolean",
  valueInteger: "valueInteger",
  valueDecimal: "valueQuantity",
  valueString: "valueString",
  valueQuantity: "valueQuantity",
  valueDate: "valueDateTime",
};
const NEEDS_TIERS = new Set(["emergency", "urgent_same_day", "routine_next_business_day"]);
const DEFAULT_PRIORITY: Record<string, string> = { emergency: "stat", urgent_same_day: "urgent", routine_next_business_day: "routine", informational: "routine", none: "routine" };

function b64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

/** transform.py _how: how a value was made, for the source map's note. */
function how(spec: JsonObject): string {
  const out = [str(spec.type)];
  if (spec.constant !== undefined && spec.constant !== null) out.push("constant");
  const concat = arr(spec.concat).map(String);
  if (concat.length) out.push(`concat ${concat.join(", ")}`);
  if (Object.keys(o(spec.value_map)).length && !concat.length) out.push("value map");
  return out.join("; ");
}

/** The preview of `profile` on the fictional sample, like POST /api/ehr-export/preview (source map included). */
export function demoPreview(profile: EhrProfile, sample = SAMPLE): EhrPreview {
  const record = sample.record;
  const callId = sample.call_id;
  const uc = useCases(profile).find((u) => str(u.campaign_id) === sample.campaign_id && (!arr(u.spec_versions).length || arr(u.spec_versions).map(Number).includes(Number(sample.call.spec_version))));
  if (!uc) return { ok: false, source_record: record, bundle: null, source_map: [], skipped: [], warnings: [], errors: [{ message: "the profile has no mapping for this use case" }] };
  const system = str(getIn(profile, ["identifiers", "system"]));
  const recItems = arr(getIn(record, ["questionnaire_response", "item"])).map(o);
  const answers: Record<string, Json> = {};
  const srcs = new Map<string, string>();
  recItems.forEach((it, i) => {
    if (it.linkId === undefined || it.linkId === null) return;
    srcs.set(str(it.linkId), `/questionnaire_response/item/${i}/answer`);
    if (!str(it.linkId).startsWith("_")) answers[str(it.linkId)] = it.answer ?? null;
  });
  const subject: JsonObject = { reference: "Patient/example-patient" };
  if (getIn(profile, ["patient", "include_display_name"]) === true && str(getIn(record, ["patient", "name"]))) subject.display = str(getIn(record, ["patient", "name"]));
  const skipped: SkippedAnswer[] = [];
  const skip = (qid: string, reason: string) => skipped.push({ question_id: qid, reason, source: srcs.get(qid) ?? null });
  const writeMode = str(getIn(profile, ["target", "write_mode"]), "conditional_update");
  const enc = o(record.encounter);
  const rev = o(record.review);
  const disp = o(record.disposition);

  // The trace (transform.py _Trace): rows for the resource being built, targets relative to it.
  let rows: SourceLink[] = [];
  const map: SourceLink[] = [];
  const addRow = (source: Src, target: string, question_id?: string, note?: string) => {
    const row: SourceLink = { source, target };
    if (question_id !== undefined) row.question_id = question_id;
    if (note) row.note = note;
    rows.push(row);
  };
  const has = (...path: string[]): string | null => {
    let cur: unknown = record;
    for (const p of path) {
      if (!cur || typeof cur !== "object" || Array.isArray(cur) || !(p in (cur as object))) return null;
      cur = (cur as JsonObject)[p];
    }
    return "/" + path.join("/");
  };
  const when = (target: string, path: string[], note?: string) => {
    const s = has(...path);
    if (s !== null) addRow(s, target, undefined, note);
  };
  const subjectRows = (...targets: string[]) => {
    const s = has("patient", "mrn");
    if (s !== null) for (const t of targets) addRow(s, t, undefined, "patient lookup (placeholder in preview)");
  };
  const identifierRow = (target: string) => addRow(record.call_sid === callId ? "/call_sid" : null, target, undefined, "identifier from the call id");
  const dtRow = (target: string, ...paths: string[][]) => {
    for (const path of paths) {
      const v = getIn(record, path);
      if (typeof v === "string" && ISO_DT.test(v)) {
        addRow("/" + path.join("/"), target);
        return;
      }
    }
  };

  const entries: { type: string; ident: string; url: string; res: JsonObject }[] = [];
  const add = (type: string, ident: string, res: JsonObject | null) => {
    const base = `/entry/${entries.length}/resource`;
    const mine = rows.map((r) => ({ ...r, target: base + r.target }));
    rows = [];
    if (!res) return null;
    const url = fullUrl(system, ident);
    map.push(...mine);
    entries.push({ type, ident, url, res });
    return url;
  };

  // QuestionnaireResponse
  const qr = uc.questionnaire_response as JsonObject | null | undefined;
  let qrUrl: string | null = null;
  if (qr && qr.enabled !== false) {
    const items: JsonObject[] = [];
    const excluded = new Set(arr(qr.exclude).map(String));
    const prefix = str(qr.unmapped_prefix);
    const itemMaps = o(qr.items);
    const itemSources = (qids: string[], j: number, note: string) => {
      for (const q of qids) {
        const s = srcs.get(q);
        addRow(s ? s.slice(0, -"/answer".length) : null, `/item/${j}`, q, note);
      }
    };
    for (const [qid, raw] of Object.entries(itemMaps)) {
      if (excluded.has(qid)) continue;
      const m = o(raw);
      const ids = arr(m.concat).length ? arr(m.concat).map(String) : [qid];
      const [good, bad] = valuesFor(m, qid, answers, srcs);
      if (good.length) {
        const item: JsonObject = { linkId: str(m.link_id) };
        if (str(m.text)) item.text = str(m.text);
        item.answer = good.map((g) => g.value);
        const j = items.length;
        itemSources(ids, j, `linkId ${str(m.link_id)}`);
        good.forEach((g, k) => {
          for (const s of g.srcs) addRow(s, `/item/${j}/answer/${k}/${Object.keys(g.value)[0]}`, qid, how(m));
        });
        items.push(item);
      }
      for (const b of bad) {
        if (str(qr.unmapped, "skip") === "text") {
          const j = items.length;
          items.push({ linkId: `${prefix}${qid}`, answer: [{ valueString: squash(b.v) }] });
          const note = "could not be typed; sent as text";
          itemSources(ids, j, note);
          for (const s of b.srcs) addRow(s, `/item/${j}/answer/0/valueString`, qid, note);
          skip(qid, note);
        } else skip(qid, `could not be typed (${str(m.type)})`);
      }
    }
    const concatSources = new Set(Object.values(itemMaps).flatMap((m) => arr(o(m).concat).map(String)));
    for (const [qid, v] of Object.entries(answers)) {
      if (qid in itemMaps || excluded.has(qid) || !answered(v) || concatSources.has(qid)) continue;
      if (str(qr.unmapped, "skip") === "text") {
        const listed = Array.isArray(v);
        const vals: { x: unknown; src: Src }[] = [];
        (listed ? v : [v]).forEach((x0, k) => {
          let x: unknown = x0;
          const b = srcs.get(qid) ?? null;
          let src: Src = listed ? (b === null ? null : `${b}/${k}`) : b;
          if (x && typeof x === "object" && !Array.isArray(x)) {
            x = (x as JsonObject).text;
            src = src === null ? null : `${src}/text`;
          }
          if (answered(x)) vals.push({ x, src });
        });
        if (vals.length) {
          const j = items.length;
          const note = "not mapped; sent as text";
          items.push({ linkId: `${prefix}${qid}`, answer: vals.map(({ x }) => ({ valueString: squash(x) })) });
          itemSources([qid], j, note);
          vals.forEach(({ src }, k) => addRow(src, `/item/${j}/answer/${k}/valueString`, qid, note));
          skip(qid, note);
        }
      } else skip(qid, "not mapped");
    }
    const res: JsonObject = { resourceType: "QuestionnaireResponse", identifier: { system, value: callId }, questionnaire: str(qr.questionnaire), status: "completed", subject };
    const authored = dt(enc.date, rev.at, record.created_at);
    if (authored) {
      res.authored = authored;
      dtRow("/authored", ["encounter", "date"], ["review", "at"], ["created_at"]);
    }
    const author = qr.author_display === undefined ? "Automated outreach (patient-reported)" : str(qr.author_display);
    if (author) res.author = { display: author };
    if (items.length) res.item = items;
    identifierRow("/identifier/value");
    subjectRows("/subject");
    qrUrl = add("QuestionnaireResponse", callId, res);
  }

  // Task
  const task = uc.task as JsonObject | null | undefined;
  if (task && task.enabled !== false) {
    const tier = str(disp.follow_up) || "none";
    const resolved = rev.resolved === true;
    const emitWhen = str(task.emit_when, "resolved");
    const emit = emitWhen === "always" || (emitWhen === "resolved" && resolved) || (emitWhen === "needs_follow_up" && (resolved || NEEDS_TIERS.has(tier)));
    if (emit) {
      const status = { resolved: "completed", open: "requested", ...o(task.status) } as JsonObject;
      const pr = Object.keys(o(task.priority)).length ? o(task.priority) : DEFAULT_PRIORITY;
      const res: JsonObject = {
        resourceType: "Task",
        identifier: [{ system, value: `${callId}-task` }],
        status: str(status[resolved ? "resolved" : "open"]),
        intent: str(task.intent, "order"),
        priority: str(pr[tier], "routine"),
        description: task.description === undefined ? "RN follow-up after automated outreach" : str(task.description),
        for: subject,
      };
      identifierRow("/identifier/0/value");
      when("/status", ["review", "resolved"], `resolved -> ${str(res.status)}`);
      when("/priority", ["disposition", "follow_up"], `tier ${tier} -> ${str(res.priority)}`);
      subjectRows("/for");
      if (task.code) res.code = { coding: [codingOf(o(task.code), str(task.code_system))] };
      if (qrUrl) res.focus = { reference: qrUrl };
      const authored = dt(record.created_at, rev.at);
      if (authored) {
        res.authoredOn = authored;
        dtRow("/authoredOn", ["created_at"], ["review", "at"]);
      }
      const modified = dt(rev.at);
      if (modified) {
        res.lastModified = modified;
        when("/lastModified", ["review", "at"]);
      }
      const signer = str(rev.by);
      const mode = str(o(task.owner).mode, "display");
      if (signer && mode === "display") {
        res.owner = { display: signer };
        when("/owner", ["review", "by"], "owner: display");
      } else if (signer && mode === "identifier") {
        res.owner = { identifier: { system: str(o(task.owner).identifier_system), value: signer }, display: signer };
        when("/owner", ["review", "by"], "owner: identifier");
      }
      if (task.include_reason !== false && str(disp.reason)) {
        res.reasonCode = { text: str(disp.reason) };
        when("/reasonCode/text", ["disposition", "reason"]);
      }
      if (task.include_nurse_note !== false && str(rev.note)) {
        const note: JsonObject = { text: str(rev.note) };
        when("/note/0/text", ["review", "note"], "the nurse's note");
        if (modified) {
          note.time = modified;
          when("/note/0/time", ["review", "at"]);
        }
        if (signer) {
          note.authorString = signer;
          when("/note/0/authorString", ["review", "by"]);
        }
        res.note = [note];
      }
      add("Task", `${callId}-task`, res);
    }
  }

  // Observations
  for (const raw of arr(uc.observations)) {
    const ob = o(raw);
    const source = str(ob.source);
    const [good, bad] = valuesFor(ob, source, answers, srcs);
    if (bad.length && !good.length) skip(source, `observation: not a ${str(ob.type)}`);
    if (!good.length) {
      add("Observation", `${callId}-obs-${source}`, null);
      continue;
    }
    const [[k, v]] = Object.entries(good[0].value);
    let value: JsonObject;
    let target: string;
    if (k === "valueCoding") {
      value = { valueCodeableConcept: { coding: [v] } };
      target = "/valueCodeableConcept/coding/0";
    } else if (k === "valueDecimal") {
      value = { valueQuantity: { value: v } };
      target = "/valueQuantity/value";
    } else {
      value = { [OBS_VALUE[k]]: v };
      target = `/${OBS_VALUE[k]}`;
    }
    for (const s of good[0].srcs) addRow(s, target, source, `observation ${str(o(ob.code).code)}; ${how(ob)}`);
    identifierRow("/identifier/0/value");
    subjectRows("/subject", "/performer/0");
    const res: JsonObject = {
      resourceType: "Observation",
      identifier: [{ system, value: `${callId}-obs-${source}` }],
      status: "final",
      category: [{ coding: [{ system: "http://terminology.hl7.org/CodeSystem/observation-category", code: str(ob.category, "survey") }] }],
      code: { coding: [codingOf(o(ob.code), str(ob.code_system))] },
      subject,
      performer: [subject],
      ...value,
    };
    const eff = dt(enc.date, rev.at);
    if (eff) {
      res.effectiveDateTime = eff;
      dtRow("/effectiveDateTime", ["encounter", "date"], ["review", "at"]);
    }
    if (qrUrl) res.derivedFrom = [{ reference: qrUrl }];
    add("Observation", `${callId}-obs-${source}`, res);
  }

  // Summary and transcript (DocumentReference / Communication)
  const warnings: string[] = [];
  for (const which of ["summary", "transcript"] as const) {
    const doc = o(uc[which]);
    if (doc.enabled !== true) continue;
    if (which === "transcript") {
      warnings.push("transcript enabled but the call has no transcript"); // the fictional sample has none
      continue;
    }
    const kind = str(doc.resource, "DocumentReference");
    const title = "Outreach call summary";
    const lines = [`Automated outreach by ${str(enc.channel) || "phone"} on ${str(enc.date) || "?"}.`, `Follow-up tier: ${str(disp.follow_up) || "none"}.`];
    if (str(disp.reason)) lines.push(`Reason: ${str(disp.reason)}`);
    if (disp.changed_by_nurse) lines.push("The tier was changed by a nurse.");
    if (str(rev.by)) lines.push(`Signed off by ${str(rev.by)} at ${str(rev.at) || "?"}.`);
    if (str(rev.note)) lines.push(`Nurse note: ${str(rev.note)}`);
    const text = lines.join("\n");
    const textTarget = kind === "Communication" ? "/payload/0/contentString" : "/content/0/attachment/data";
    identifierRow("/identifier/0/value");
    subjectRows("/subject");
    dtRow(kind === "Communication" ? "/sent" : "/date", ["review", "at"], ["created_at"]);
    for (const part of ["encounter", "disposition", "review"]) when(textTarget, [part], "summary text");
    const ident = `${callId}-${which}`;
    const at = dt(rev.at, record.created_at);
    const identifier = [{ system, value: ident }];
    let res: JsonObject;
    if (kind === "Communication") {
      res = { resourceType: "Communication", identifier, status: "completed", subject, payload: [{ contentString: text }] };
      if (at) res.sent = at;
      if (qrUrl) res.about = [{ reference: qrUrl }];
    } else {
      res = {
        resourceType: "DocumentReference",
        identifier,
        status: "current",
        docStatus: "final",
        subject,
        description: title,
        content: [{ attachment: { contentType: "text/plain; charset=utf-8", data: b64(text), title } }],
      };
      if (doc.type) res.type = { coding: [codingOf(o(doc.type), str(doc.type_system))] };
      if (at) res.date = at;
      if (qrUrl) res.context = { related: [{ reference: qrUrl }] };
    }
    add(kind, ident, res);
  }

  // Excluded answers are listed too (exporter._skipped_rows).
  if (qr && qr.enabled !== false) for (const qid of arr(qr.exclude).map(String)) if (qid in answers && answered(answers[qid])) skip(qid, "excluded by the profile");

  const bundle: JsonObject = {
    resourceType: "Bundle",
    type: "transaction",
    entry: entries.map((e) => ({
      fullUrl: e.url,
      resource: e.res,
      request: (writeMode === "conditional_create"
        ? { method: "POST", url: e.type, ifNoneExist: `identifier=${quote(`${system}|${e.ident}`)}` }
        : { method: "PUT", url: `${e.type}?identifier=${quote(`${system}|${e.ident}`)}` }) as JsonObject,
    })),
  };
  return { ok: true, source_record: record, bundle, source_map: map, skipped, warnings, errors: [] };
}

// --------------------------------------------------------------------------- simulate (exporter.simulate on the fake FHIR server, abridged)

/** Two export runs of the preview's bundle against an in-memory server: what POST /simulate returns. */
export function demoSimulate(profile: EhrProfile, preview: EhrPreview): EhrSimulation {
  const entries = arr(getIn(preview.bundle, ["entry"])).map(o);
  const mrn = str(getIn(SAMPLE.record, ["patient", "mrn"]));
  const search = `Patient?identifier=${quote(`${str(getIn(profile, ["patient", "identifier_system"]))}|${mrn}`)}`;
  const transaction = str(getIn(profile, ["target", "submit"]), "transaction") === "transaction";
  const conditionalCreate = str(getIn(profile, ["target", "write_mode"]), "conditional_update") === "conditional_create";
  const refs = entries.map((e, i) => `${str(o(e.resource).resourceType)}/${i + 2}`); // the fake patient is 1
  const ids = new Map<string, string>(entries.map((e, i) => [str(e.fullUrl), refs[i]]));
  const swap = (v: Json): Json => {
    if (typeof v === "string") return v === "Patient/example-patient" ? "Patient/1" : (ids.get(v) ?? v);
    if (Array.isArray(v)) return v.map(swap);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, swap(x)]));
    return v;
  };
  const sorted = [...refs].sort();
  const runs = entries.length
    ? [0, 1].map((run) => {
        const requests: SimRequest[] = [{ method: "GET", url: search, status: 200 }];
        if (transaction) requests.push({ method: "POST", url: "(transaction)", status: 200 });
        else for (const e of entries) requests.push({ method: str(o(e.request).method), url: str(o(e.request).url), status: run === 0 ? 201 : 200 });
        const created = run === 0 ? sorted : [];
        const updated = run === 0 || conditionalCreate ? [] : sorted;
        return { requests, created: created.length, updated: updated.length, created_refs: created, updated_refs: updated, outcome: { status: "exported", error: null, refs } };
      })
    : [];
  const resources = entries.map((e, i) => ({ ...(swap(o(e.resource)) as JsonObject), id: String(i + 2), meta: { versionId: conditionalCreate ? "1" : "2" } }) as Json);
  return { runs, resources };
}
// --------------------------------------------------------------------------- versions and outbox (held in this tab)

export function demoVersions(): EhrVersion[] {
  return [
    { version: 2, status: "draft", profile: EXAMPLE_PROFILE, problems: EXAMPLE_PROBLEMS, saved_by: "admin.one@example.org", saved_at: "2026-10-22T10:05:00.000+00:00", note: "Draft: same as active" },
    {
      version: 1,
      status: "active",
      profile: EXAMPLE_PROFILE,
      problems: EXAMPLE_PROBLEMS,
      saved_by: "admin.one@example.org",
      saved_at: "2026-10-21T09:00:00.000+00:00",
      activated_by: "admin.two@example.org",
      activated_at: "2026-10-21T09:30:00.000+00:00",
      note: "Started from the example",
    },
  ];
}

export function demoOutbox(): OutboxRecord[] {
  return [
    { call_id: "CAexample0001", status: "pending_export", created_at: "2026-10-21T16:20:00.000+00:00", attempts: 0 },
    { call_id: "CAexample0002", status: "exported", created_at: "2026-10-21T11:02:00.000+00:00", exported_at: "2026-10-21T11:03:10.000+00:00", attempts: 1 },
    { call_id: "CAexample0003", status: "failed", created_at: "2026-10-20T15:40:00.000+00:00", last_error: "patient_ambiguous", attempts: 1 },
    { call_id: "CAexample0004", status: "skipped", created_at: "2026-10-20T09:12:00.000+00:00", last_error: "no_mapping", attempts: 1 },
    { call_id: "CAexample0005", status: "withdrawn_done", created_at: "2026-10-19T14:00:00.000+00:00", attempts: 2 },
  ];
}
