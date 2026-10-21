/**
 * Admin → Knowledge base (docs/KNOWLEDGE-PLAN.md v2, hybrid): example Q&A and
 * guidelines the knowledge agent answers from, its mode (off / shadow / live)
 * and "Try it".
 *
 * Types follow the API (/api/admin/knowledge/*, plan §5). The pure helpers
 * here are the panel's rules (filtering, what each status allows, draft and
 * file validation, plain-words reasons) and the sample-data "Try it", which
 * only imitates the backend: classification, drafting and the grounding
 * check run on the server.
 */

import type { CareSetting, KnowledgeSource } from "./types";

export type KnowledgeStatus = "draft" | "approved" | "retired";

export interface KnowledgeItem {
  item_id: string;
  question: string;
  alt_questions: string[];
  answer: string;
  category: string;
  category_label: string;
  status: KnowledgeStatus;
  version: number;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
  approved_at: string | null;
  approved_by: string | null;
}

export interface KnowledgeCategory {
  key: string;
  label: string;
}

export type KnowledgeMode = "off" | "shadow" | "live";

export interface KnowledgeSettings {
  /** off: every question to a nurse; shadow: drafts after the call; live: answers on the call. */
  mode: KnowledgeMode;
  channels: { voice: boolean; sms: boolean };
  allowed_categories: string[];
  /** 0.5–1: the grounding check's minimum support score for an answer. */
  min_support: number;
  ask_satisfied: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
  /** Read-only: never answered by the agent, whatever the settings. */
  locked_categories: string[];
  /** Read-only: every category an entry may have. */
  available_categories: KnowledgeCategory[];
}

/** What PUT /api/admin/knowledge/settings accepts (partial). */
export type KnowledgeSettingsPatch = Partial<
  Pick<KnowledgeSettings, "mode" | "channels" | "allowed_categories" | "min_support" | "ask_satisfied">
>;

export interface KnowledgeItemInput {
  question: string;
  answer: string;
  category: string;
  alt_questions: string[];
}

/** A guideline: a titled block of clinic text, optionally for some procedures or care settings. */
export interface KnowledgeDoc {
  doc_id: string;
  title: string;
  body: string;
  procedures: string[];
  care_settings: CareSetting[];
  status: KnowledgeStatus;
  version: number;
  chars: number;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
  approved_at: string | null;
  approved_by: string | null;
}

export interface KnowledgeDocInput {
  title: string;
  body: string;
  procedures: string[];
  care_settings: CareSetting[];
}

/** Optional patient context for "Try it" (never a name, DOB, phone or MRN). */
export interface KnowledgeTryContext {
  procedure?: string;
  care_setting?: CareSetting;
  days_since_surgery?: number;
  is_diabetic?: boolean;
}

export interface KnowledgeClassification {
  category: string;
  category_label: string;
  llm_category: string | null;
  keyword_category: string | null;
  red_flag: boolean;
  vetoed_by: "keywords" | "llm" | null;
}

export interface KnowledgeTryResult {
  outcome: "answered" | "deferred";
  reason: string;
  classification?: KnowledgeClassification | null;
  answer?: string | null;
  sources?: KnowledgeSource[];
  support_score?: number | null;
  latency_ms?: number | null;
  note?: string | null;
}

// ---- Limits (the API enforces the same) ----------------------------------

export const MAX_QUESTION = 300;
export const MAX_ANSWER = 600;
export const MAX_ALT_QUESTIONS = 10;
export const MIN_SUPPORT_RANGE = { min: 0.5, max: 1, step: 0.01 } as const;
export const DEFAULT_MIN_SUPPORT = 0.85;

export const MAX_DOC_TITLE = 120;
export const MAX_DOC_BODY = 20_000;
export const MAX_DOCS = 100;
export const MAX_PROCEDURES = 20;
/** One procedure name (not in plan §5; matches the API's voa_core.knowledge.MAX_PROCEDURE). */
export const MAX_PROCEDURE = 120;
/** Approved guideline characters in total (approving beyond is a 409). */
export const APPROVED_CHARS_BUDGET = 200_000;

export const CARE_SETTINGS: readonly CareSetting[] = ["inpatient", "outpatient"];
export const CARE_SETTING_LABEL: Record<CareSetting, string> = { inpatient: "Inpatient", outpatient: "Outpatient" };

export const MODE_LABEL: Record<KnowledgeMode, string> = { off: "Off", shadow: "Shadow", live: "Live" };
export const MODE_HINT: Record<KnowledgeMode, string> = {
  off: "Every question goes to a nurse.",
  shadow: "Questions still go to a nurse; the agent drafts an answer after the call for nurses to rate.",
  live: "The agent answers when every check passes.",
};

/** Never answered by the agent; the API sends the same list as locked_categories. */
export const LOCKED_CATEGORIES = ["medication", "symptom"] as const;

export const STATUS_LABEL: Record<KnowledgeStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  retired: "Retired",
};

// ---- What each status allows --------------------------------------------

/** Only drafts and retired entries can be deleted; an approved one must be retired first. */
export function canDelete(item: Pick<KnowledgeItem, "status">): boolean {
  return item.status !== "approved";
}

export function canApprove(item: Pick<KnowledgeItem, "status">): boolean {
  return item.status !== "approved";
}

export function canRetire(item: Pick<KnowledgeItem, "status">): boolean {
  return item.status === "approved";
}

/**
 * A draft that was approved before and then edited: the API sends an edited
 * approved entry back to draft with version + 1, so a draft past version 1,
 * or one that still carries an approval, needs re-approval.
 */
export function needsReapproval(item: Pick<KnowledgeItem, "status" | "version" | "approved_at">): boolean {
  return item.status === "draft" && (item.version > 1 || !!item.approved_at);
}

// ---- Filtering ------------------------------------------------------------

export type StatusFilter = KnowledgeStatus | "all";

/** Status filter plus a case-insensitive search of the question, phrasings, answer and category. */
export function filterItems(items: KnowledgeItem[], status: StatusFilter, search: string): KnowledgeItem[] {
  const needle = search.trim().toLowerCase();
  return items.filter((it) => {
    if (status !== "all" && it.status !== status) return false;
    if (!needle) return true;
    return [it.question, it.answer, it.category_label, it.category, ...it.alt_questions]
      .some((s) => (s ?? "").toLowerCase().includes(needle));
  });
}

const STATUS_ORDER: Record<KnowledgeStatus, number> = { draft: 0, approved: 1, retired: 2 };

/** Drafts first (they need action), then approved, then retired; newest change first within each. */
export function sortItems(items: KnowledgeItem[]): KnowledgeItem[] {
  return [...items].sort(
    (a, b) =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
      (b.updated_at ?? "").localeCompare(a.updated_at ?? "") ||
      a.question.localeCompare(b.question),
  );
}

export function statusCounts(items: Pick<KnowledgeItem, "status">[]): Record<StatusFilter, number> {
  const out: Record<StatusFilter, number> = { all: items.length, draft: 0, approved: 0, retired: 0 };
  for (const it of items) out[it.status] += 1;
  return out;
}

// ---- Editing --------------------------------------------------------------

/** One phrasing per line: trimmed, blanks and repeats (and the main question) dropped. */
export function parseAltQuestions(text: string, mainQuestion = ""): string[] {
  const main = mainQuestion.trim().toLowerCase();
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const v = line.trim().replace(/\s+/g, " ");
    const k = v.toLowerCase();
    if (!v || k === main || seen.has(k)) continue;
    seen.add(k);
    out.push(v);
  }
  return out;
}

/** The first problem with a draft entry, or null when it can be saved. */
export function itemDraftError(d: KnowledgeItemInput, categories: string[]): string | null {
  const q = d.question.trim();
  const a = d.answer.trim();
  if (!q) return "Write the patient's question.";
  if (q.length > MAX_QUESTION) return `The question is longer than ${MAX_QUESTION} characters.`;
  if (!a) return "Write the answer patients will hear.";
  if (a.length > MAX_ANSWER) return `The answer is longer than ${MAX_ANSWER} characters.`;
  if (d.alt_questions.length > MAX_ALT_QUESTIONS) return `At most ${MAX_ALT_QUESTIONS} alternate phrasings.`;
  const long = d.alt_questions.find((x) => x.length > MAX_QUESTION);
  if (long) return `An alternate phrasing is longer than ${MAX_QUESTION} characters.`;
  if (!d.category) return "Choose a category.";
  if (!categories.includes(d.category)) return "Choose one of the listed categories.";
  return null;
}

/** Categories an entry may be filed under: the available ones, never a locked one. */
export function editableCategories(s: Pick<KnowledgeSettings, "available_categories" | "locked_categories">): KnowledgeCategory[] {
  const locked = new Set(s.locked_categories);
  return s.available_categories.filter((c) => !locked.has(c.key));
}

/** Toggle an allowed category; a locked one can never be turned on. */
export function toggleCategory(allowed: string[], key: string, locked: readonly string[]): string[] {
  if (locked.includes(key)) return allowed.filter((k) => k !== key);
  return allowed.includes(key) ? allowed.filter((k) => k !== key) : [...allowed, key];
}

export function clampMinSupport(v: number): number {
  if (!Number.isFinite(v)) return MIN_SUPPORT_RANGE.min;
  return Math.round(Math.min(MIN_SUPPORT_RANGE.max, Math.max(MIN_SUPPORT_RANGE.min, v)) * 100) / 100;
}

export const KNOWLEDGE_MODES: readonly KnowledgeMode[] = ["off", "shadow", "live"];

/**
 * Settings as the panel uses them. Tolerates a step-1 document while the
 * backend rolls out: `enabled: true` reads as live, `false` as off (plan §5),
 * and a missing min_support takes the default.
 */
export function normaliseSettings(raw: unknown): KnowledgeSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const mode: KnowledgeMode = KNOWLEDGE_MODES.includes(r.mode as KnowledgeMode)
    ? (r.mode as KnowledgeMode)
    : r.enabled === true ? "live" : "off";
  const ch = (r.channels && typeof r.channels === "object" ? r.channels : {}) as Record<string, unknown>;
  const strs = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  const cats = Array.isArray(r.available_categories)
    ? (r.available_categories as unknown[]).flatMap((c) => {
        const o = c as Record<string, unknown> | null;
        return o && typeof o.key === "string" ? [{ key: o.key, label: typeof o.label === "string" ? o.label : o.key }] : [];
      })
    : [];
  return {
    mode,
    channels: { voice: ch.voice !== false, sms: ch.sms === true },
    allowed_categories: strs(r.allowed_categories),
    min_support: typeof r.min_support === "number" ? clampMinSupport(r.min_support) : DEFAULT_MIN_SUPPORT,
    ask_satisfied: r.ask_satisfied !== false,
    updated_at: typeof r.updated_at === "string" ? r.updated_at : null,
    updated_by: typeof r.updated_by === "string" ? r.updated_by : null,
    locked_categories: Array.isArray(r.locked_categories) ? strs(r.locked_categories) : [...LOCKED_CATEGORIES],
    available_categories: cats,
  };
}

/**
 * What to warn about before switching to live. Ratings come from the
 * recent calls the console has loaded (null: not known).
 */
export function liveWarnings(p: { approvedItems: number; approvedDocs: number; ratings: number | null }): string[] {
  const out: string[] = [];
  if (p.approvedItems + p.approvedDocs === 0) {
    out.push("Nothing is approved yet: no example Q&A and no guidelines. Every question will still go to a nurse.");
  }
  if (p.ratings === 0) {
    out.push("Nurses have not rated any shadow drafts in the recent calls, so there is no evidence yet of how often the agent is right.");
  } else if (p.ratings === null) {
    out.push("Shadow ratings could not be counted. Check how nurses rated the agent's drafts before going live.");
  }
  return out;
}

/** Only the fields that changed, for the partial PUT. */
export function settingsPatch(saved: KnowledgeSettings, draft: KnowledgeSettings): KnowledgeSettingsPatch {
  const p: KnowledgeSettingsPatch = {};
  if (draft.mode !== saved.mode) p.mode = draft.mode;
  if (draft.channels.voice !== saved.channels.voice || draft.channels.sms !== saved.channels.sms) p.channels = { ...draft.channels };
  const a = [...draft.allowed_categories].sort().join(",");
  const b = [...saved.allowed_categories].sort().join(",");
  if (a !== b) p.allowed_categories = [...draft.allowed_categories];
  if (clampMinSupport(draft.min_support) !== clampMinSupport(saved.min_support)) p.min_support = clampMinSupport(draft.min_support);
  if (draft.ask_satisfied !== saved.ask_satisfied) p.ask_satisfied = draft.ask_satisfied;
  return p;
}

// ---- Sample data (?data=demo) -------------------------------------------
//
// Fictional clinic Q&A. Nothing here is sent anywhere.

export const DEMO_CATEGORIES: KnowledgeCategory[] = [
  { key: "appointment", label: "Appointment" },
  { key: "activity", label: "Activity" },
  { key: "diet", label: "Diet" },
  { key: "paperwork_billing", label: "Paperwork or billing" },
  { key: "supplies", label: "Supplies" },
  { key: "recovery", label: "Recovery timeline" },
  { key: "medication", label: "Medication" },
  { key: "symptom", label: "Symptom or wound" },
];

export function demoSettings(): KnowledgeSettings {
  return {
    mode: "shadow",
    channels: { voice: true, sms: false },
    allowed_categories: ["appointment", "activity", "diet", "paperwork_billing", "supplies", "recovery"],
    min_support: DEFAULT_MIN_SUPPORT,
    ask_satisfied: true,
    updated_at: new Date(Date.UTC(2026, 9, 18, 14, 0)).toISOString(),
    updated_by: "dr.rivera@example.org",
    locked_categories: [...LOCKED_CATEGORIES],
    available_categories: DEMO_CATEGORIES,
  };
}

export function categoryLabelOf(key: string, categories: KnowledgeCategory[] = DEMO_CATEGORIES): string {
  const found = categories.find((c) => c.key === key)?.label;
  if (found) return found;
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function demoItem(
  id: string,
  question: string,
  alt: string[],
  answer: string,
  category: string,
  status: KnowledgeStatus,
  version: number,
  daysAgo: number,
): KnowledgeItem {
  const at = new Date(Date.UTC(2026, 9, 20 - daysAgo, 15, 0)).toISOString();
  const approved = status !== "draft" || version > 1;
  return {
    item_id: id,
    question,
    alt_questions: alt,
    answer,
    category,
    category_label: categoryLabelOf(category),
    status,
    version,
    created_at: at,
    created_by: "rn.lead@example.org",
    updated_at: at,
    updated_by: "rn.lead@example.org",
    approved_at: approved ? at : null,
    approved_by: approved ? "dr.rivera@example.org" : null,
  };
}

export function demoItems(): KnowledgeItem[] {
  return [
    demoItem(
      "kq_shower",
      "When can I shower?",
      ["Can I take a shower?", "Is it OK to get my bandage wet in the shower?"],
      "You can shower 48 hours after your surgery. Let the water run over the area, pat it dry, and don't scrub it. Please don't take a bath or swim until your surgeon says so.",
      "activity", "approved", 2, 9,
    ),
    demoItem(
      "kq_drive",
      "When can I drive again?",
      ["Am I allowed to drive?", "How long until I can drive a car?"],
      "Please don't drive while you are taking prescription pain medicine, and not for at least two weeks after surgery. Your surgeon will confirm at your follow-up visit.",
      "activity", "approved", 1, 7,
    ),
    demoItem(
      "kq_parking",
      "Where do I park for my follow-up visit?",
      ["Where is parking?", "Is there parking at the clinic?"],
      "Park in the visitor garage on Elm Street, level 2. Bring your ticket to the front desk and we will validate it.",
      "appointment", "approved", 1, 5,
    ),
    demoItem(
      "kq_diet",
      "What can I eat after surgery?",
      ["Is there anything I shouldn't eat?"],
      "Start with light foods like soup, toast and crackers, and drink plenty of water. You can go back to your usual diet as you feel ready.",
      "diet", "draft", 2, 1,
    ),
    demoItem(
      "kq_forms",
      "How do I get a work note?",
      ["Can I get a letter for my employer?"],
      "Call the front desk and ask for a work note. We can email it to you within two business days.",
      "paperwork_billing", "draft", 1, 0,
    ),
    demoItem(
      "kq_ice",
      "Where can I buy an ice pack?",
      [],
      "Any pharmacy sells reusable ice packs. A bag of frozen peas wrapped in a towel works too.",
      "supplies", "retired", 1, 30,
    ),
  ];
}

// ---- Reasons and classification, in plain words ---------------------------

/** Every `reason` the API sends (plan §5). */
export const REASONS = [
  "answered", "empty_question", "red_flag", "locked_category", "unclassified", "category_not_allowed",
  "low_confidence", "no_knowledge", "not_covered", "low_support", "output_veto", "timeout", "error",
] as const;
export type KnowledgeReason = (typeof REASONS)[number];

const pct = (v: number) => `${Math.round(v * 100)}%`;

/**
 * The reason as a sentence for clinicians. `category_label` names the topic
 * for category_not_allowed; the scores make low_support specific. An unknown
 * reason reads as its code in words, so a new backend reason never shows blank.
 */
export function reasonText(
  reason: string | null | undefined,
  ctx: { category_label?: string | null; support_score?: number | null; min_support?: number | null } = {},
): string {
  switch (reason) {
    case "answered":
      return "Every check passed: the answer comes only from approved material, and the grounding check supports it.";
    case "empty_question":
      return "There was no question to answer.";
    case "red_flag":
      return "It sounds like it could be urgent, so a nurse calls back. Possible red flags are never answered by the agent.";
    case "locked_category":
      return "Medication and symptom questions are never answered by the agent. A nurse calls back.";
    case "unclassified":
      return "The agent could not tell what the question is about, so a nurse calls back.";
    case "category_not_allowed":
      return ctx.category_label
        ? `${ctx.category_label} questions are switched off in the settings.`
        : "Questions on this topic are switched off in the settings.";
    case "low_confidence":
      return "The agent was not sure what the question is about, so a nurse calls back.";
    case "no_knowledge":
      return "No approved example or guideline applies to this patient.";
    case "not_covered":
      return "The approved material does not clearly cover this question for this patient.";
    case "low_support": {
      const s = typeof ctx.support_score === "number" ? ` (support ${pct(ctx.support_score)}` : "";
      const m = typeof ctx.min_support === "number" ? `${s ? ", " : " ("}minimum ${pct(ctx.min_support)})` : s ? ")" : "";
      return `The draft answer was not fully backed by the approved material${s}${m}.`;
    }
    case "output_veto":
      return "The draft answer mentioned medication, a dose or a symptom, so it was withheld.";
    case "timeout":
      return "The agent ran out of time, so a nurse calls back.";
    case "error":
      return "Something went wrong while checking the question, so a nurse calls back.";
    default:
      return reason ? `${reason.charAt(0).toUpperCase()}${reason.slice(1).replace(/_/g, " ")}.` : "No reason was given.";
  }
}

const SHORT_REASON: Record<KnowledgeReason, string> = {
  answered: "every check passed",
  empty_question: "there was no question",
  red_flag: "it could be urgent",
  locked_category: "it is a medication or symptom question",
  unclassified: "the topic was unclear",
  category_not_allowed: "the topic is switched off",
  low_confidence: "the topic was unclear",
  no_knowledge: "no approved material applies to this patient",
  not_covered: "the approved material does not cover it",
  low_support: "the draft was not fully backed by the approved material",
  output_veto: "the draft mentioned medication, a dose or a symptom",
  timeout: "it ran out of time",
  error: "something went wrong",
};

/** A short phrase for "The agent would have deferred: …". */
export function shortReason(reason: string | null | undefined): string {
  if (reason && reason in SHORT_REASON) return SHORT_REASON[reason as KnowledgeReason];
  return reason ? reason.replace(/_/g, " ") : "no reason was given";
}

export interface ClassificationSummary {
  /** The topic used: "Diet". */
  category: string;
  /** What the AI classifier said, or "—". */
  llm: string;
  /** What the keyword rules said, or "—". */
  keywords: string;
  /** The two sides named the same topic. */
  agree: boolean;
  redFlag: boolean;
  /** "Blocked by keyword rules: medication", or null when nothing vetoed. */
  veto: string | null;
}

/** The classification row of "Try it", in words. */
export function classificationSummary(
  c: KnowledgeClassification,
  categories: KnowledgeCategory[] = DEMO_CATEGORIES,
  locked: readonly string[] = LOCKED_CATEGORIES,
): ClassificationSummary {
  const label = (k: string | null | undefined) => (k ? categoryLabelOf(k, categories) : "—");
  const why = (k: string | null | undefined) => {
    if (k && locked.includes(k)) return label(k).toLowerCase();
    if (c.red_flag) return "possible red flag";
    return k ? label(k).toLowerCase() : "unclear question";
  };
  let veto: string | null = null;
  if (c.vetoed_by === "keywords") veto = `Blocked by keyword rules: ${why(c.keyword_category)}`;
  else if (c.vetoed_by === "llm") veto = `Blocked by the AI classifier: ${why(c.llm_category)}`;
  return {
    category: c.category_label || label(c.category),
    llm: label(c.llm_category),
    keywords: label(c.keyword_category),
    agree: !!c.llm_category && c.llm_category === c.keyword_category,
    redFlag: !!c.red_flag,
    veto,
  };
}

/** "93%" for a 0–1 score, or null. */
export function scoreText(v: number | null | undefined): string | null {
  return typeof v === "number" && Number.isFinite(v) ? pct(v) : null;
}

/** "Example · When can I shower? · v2". */
export function sourceLabel(s: KnowledgeSource): string {
  const kind = s.kind === "guideline" ? "Guideline" : "Example";
  return `${kind} · ${s.title || s.id}${typeof s.version === "number" ? ` · v${s.version}` : ""}`;
}

// ---- Guidelines -------------------------------------------------------------

/** Status filter plus a case-insensitive search of the title, body and applies-to. */
export function filterDocs(docs: KnowledgeDoc[], status: StatusFilter, search: string): KnowledgeDoc[] {
  const needle = search.trim().toLowerCase();
  return docs.filter((d) => {
    if (status !== "all" && d.status !== status) return false;
    if (!needle) return true;
    return [d.title, d.body, ...d.procedures, ...d.care_settings].some((s) => (s ?? "").toLowerCase().includes(needle));
  });
}

export function sortDocs(docs: KnowledgeDoc[]): KnowledgeDoc[] {
  return [...docs].sort(
    (a, b) =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
      (b.updated_at ?? "").localeCompare(a.updated_at ?? "") ||
      a.title.localeCompare(b.title),
  );
}

/** Characters in approved guidelines (the 200,000 budget). */
export function approvedChars(docs: Pick<KnowledgeDoc, "status" | "chars" | "body">[]): number {
  return docs.reduce((n, d) => n + (d.status === "approved" ? docChars(d) : 0), 0);
}

function docChars(d: Pick<KnowledgeDoc, "chars" | "body">): number {
  return typeof d.chars === "number" ? d.chars : d.body.length;
}

/** Whether approving this guideline would go over the budget (the API refuses with 409). */
export function approvalOverBudget(doc: KnowledgeDoc, docs: KnowledgeDoc[]): boolean {
  const others = approvedChars(docs.filter((d) => d.doc_id !== doc.doc_id));
  return others + docChars(doc) > APPROVED_CHARS_BUDGET;
}

/** Add procedure tags from typed text (comma or new line between names): trimmed, no repeats. */
export function addProcedures(current: string[], text: string): string[] {
  const seen = new Set(current.map((p) => p.toLowerCase()));
  const out = [...current];
  for (const raw of text.split(/[,\n]/)) {
    const v = raw.trim().replace(/\s+/g, " ");
    if (!v || seen.has(v.toLowerCase())) continue;
    seen.add(v.toLowerCase());
    out.push(v);
  }
  return out;
}

/** The first problem with a guideline draft, or null when it can be saved. */
export function docDraftError(d: KnowledgeDocInput): string | null {
  const title = d.title.trim();
  if (!title) return "Give the guideline a title.";
  if (title.length > MAX_DOC_TITLE) return `The title is longer than ${MAX_DOC_TITLE} characters.`;
  if (!d.body.trim()) return "Paste or load the guideline text.";
  if (d.body.length > MAX_DOC_BODY) return `The text is longer than ${MAX_DOC_BODY.toLocaleString("en-US")} characters.`;
  if (d.procedures.length > MAX_PROCEDURES) return `At most ${MAX_PROCEDURES} procedures.`;
  if (d.procedures.some((p) => p.length > MAX_PROCEDURE)) return `A procedure name is longer than ${MAX_PROCEDURE} characters.`;
  if (d.care_settings.some((c) => !CARE_SETTINGS.includes(c))) return "Choose inpatient, outpatient, or neither.";
  return null;
}

/** "Everyone", or "Knee arthroscopy · Outpatient". */
export function appliesToText(d: Pick<KnowledgeDoc, "procedures" | "care_settings">): string {
  const parts = [...d.procedures, ...d.care_settings.map((c) => CARE_SETTING_LABEL[c] ?? c)];
  return parts.length ? parts.join(" · ") : "Everyone";
}

/**
 * Whether a guideline applies to a patient (plan §5): every listed condition
 * must hold; a procedure matches case-insensitively as a substring either way;
 * no procedures or no care settings means any.
 */
export function docApplies(d: Pick<KnowledgeDoc, "procedures" | "care_settings">, ctx: KnowledgeTryContext = {}): boolean {
  if (d.care_settings.length && !(ctx.care_setting && d.care_settings.includes(ctx.care_setting))) return false;
  if (!d.procedures.length) return true;
  const p = (ctx.procedure ?? "").trim().toLowerCase();
  if (!p) return false;
  return d.procedures.some((x) => {
    const q = x.trim().toLowerCase();
    return !!q && (q.includes(p) || p.includes(q));
  });
}

// ---- Loading a guideline from a file (read in the browser) --------------------

export const GUIDELINE_FILE_TYPES = [".txt", ".md", ".markdown"] as const;
/** Accept attribute for the file input. */
export const GUIDELINE_FILE_ACCEPT = ".txt,.md,.markdown,text/plain,text/markdown";

/**
 * Before reading: only .txt or .md, and not so big it cannot fit (a
 * character can take up to 4 bytes in UTF-8, so the byte check is loose and
 * the real limit is checked on the text).
 */
export function guidelineFileError(name: string, sizeBytes: number): string | null {
  const lower = name.trim().toLowerCase();
  if (!GUIDELINE_FILE_TYPES.some((ext) => lower.endsWith(ext))) {
    return "Only plain text (.txt) or Markdown (.md) files can be loaded. Copy and paste the text from other documents.";
  }
  if (sizeBytes <= 0) return "The file is empty.";
  if (sizeBytes > MAX_DOC_BODY * 4) return `The file is too big: a guideline holds at most ${MAX_DOC_BODY.toLocaleString("en-US")} characters.`;
  return null;
}

/** After reading: not empty, not over the limit, and text rather than a binary file. */
export function guidelineTextError(text: string): string | null {
  if (!text.trim()) return "The file is empty.";
  if (text.includes("\u0000")) return "That file is not plain text.";
  if (text.length > MAX_DOC_BODY) {
    return `The file has ${text.length.toLocaleString("en-US")} characters; a guideline holds at most ${MAX_DOC_BODY.toLocaleString("en-US")}. Split it into smaller guidelines.`;
  }
  return null;
}

/** A title from a file name: "knee-arthroscopy_lifting.md" -> "Knee arthroscopy lifting". */
export function titleFromFileName(name: string): string {
  const base = name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  return base ? (base.charAt(0).toUpperCase() + base.slice(1)).slice(0, MAX_DOC_TITLE) : "";
}

/** File text as the body: a leading BOM dropped, Windows line ends made plain. */
export function normaliseFileText(text: string): string {
  return text.replace(/^﻿/, "").replace(/\r\n?/g, "\n");
}

// ---- Sample guidelines and "Try it" (?data=demo) -----------------------------

function demoDoc(
  id: string,
  title: string,
  body: string,
  procedures: string[],
  care_settings: CareSetting[],
  status: KnowledgeStatus,
  version: number,
  daysAgo: number,
): KnowledgeDoc {
  const at = new Date(Date.UTC(2026, 9, 20 - daysAgo, 16, 0)).toISOString();
  const approved = status !== "draft" || version > 1;
  return {
    doc_id: id, title, body, procedures, care_settings, status, version, chars: body.length,
    created_at: at, created_by: "rn.lead@example.org", updated_at: at, updated_by: "rn.lead@example.org",
    approved_at: approved ? at : null, approved_by: approved ? "dr.rivera@example.org" : null,
  };
}

export function demoDocs(): KnowledgeDoc[] {
  return [
    demoDoc(
      "kg_knee_lifting",
      "Lifting after outpatient knee arthroscopy",
      "After outpatient knee arthroscopy, lift nothing heavier than 10 pounds for two weeks. " +
        "Carrying a light bag or a gallon of milk is fine. Use the stairs one step at a time, leading with the good leg going up " +
        "and the operated leg going down. Walking short distances several times a day helps recovery.",
      ["knee arthroscopy"], ["outpatient"], "approved", 1, 6,
    ),
    demoDoc(
      "kg_hip_precautions",
      "Hip replacement: sitting and bending",
      "For six weeks after a hip replacement, do not bend your hip past a right angle. Sit in a firm chair with arms, " +
        "keep your knees lower than your hips, and do not cross your legs. Use the reacher to pick things up from the floor.",
      ["hip arthroplasty", "hip replacement"], [], "approved", 2, 12,
    ),
    demoDoc(
      "kg_inpatient_visitors",
      "Visiting hours on the surgical ward",
      "Visitors are welcome from 9 in the morning to 8 in the evening. Two visitors at a time can sit at the bedside. " +
        "Children under 12 visit with an adult.",
      [], ["inpatient"], "draft", 1, 1,
    ),
    demoDoc(
      "kg_bathing",
      "Bathing and swimming after surgery",
      "Do not take a bath, use a hot tub or swim until your incision has fully closed and your surgeon has checked it, usually " +
        "at the two-week visit. Showers are fine from 48 hours after surgery.",
      [], [], "retired", 1, 40,
    ),
  ];
}

let demoDocSeq = 0;

export function demoCreateDoc(input: KnowledgeDocInput, now = new Date()): KnowledgeDoc {
  demoDocSeq += 1;
  const at = now.toISOString();
  return {
    doc_id: `kg_demo_${now.getTime().toString(36)}_${demoDocSeq}`,
    title: input.title.trim(), body: input.body, procedures: input.procedures, care_settings: input.care_settings,
    status: "draft", version: 1, chars: input.body.length,
    created_at: at, created_by: "you (sample)", updated_at: at, updated_by: "you (sample)",
    approved_at: null, approved_by: null,
  };
}

/** The API's edit rule: an approved guideline goes back to draft with version + 1. */
export function demoEditDoc(doc: KnowledgeDoc, input: KnowledgeDocInput, now = new Date()): KnowledgeDoc {
  const wasApproved = doc.status === "approved";
  return {
    ...doc,
    title: input.title.trim(), body: input.body, procedures: input.procedures, care_settings: input.care_settings,
    chars: input.body.length,
    status: wasApproved ? "draft" : doc.status,
    version: wasApproved ? doc.version + 1 : doc.version,
    updated_at: now.toISOString(), updated_by: "you (sample)",
  };
}

// The sample "Try it": keyword rules, nothing like the real classifier or check.
const DEMO_MEDICATION = /\b(pill|pills|ibuprofen|tylenol|aspirin|medication|medicine|meds|dose|antibiotic|blood thinner|prescription|refill)\b/i;
const DEMO_SYMPTOM = /\b(pain|hurt|hurts|fever|bleed|bleeding|swollen|swelling|red|redness|pus|drainage|wound|incision|stitches|dizzy|nausea)\b/i;
const DEMO_RED_FLAG = /\b(chest pain|can'?t breathe|short of breath|passed out|faint|bleeding a lot|soaked)\b/i;
const DEMO_TOPICS: [RegExp, string][] = [
  [/\b(park|parking|appointment|visit|visiting|visitor|follow-?up)\b/i, "appointment"],
  [/\b(shower|bath|swim|drive|driving|lift|lifting|carry|stairs|walk|bend|sit|exercise|work)\b/i, "activity"],
  [/\b(eat|food|drink|wine|beer|alcohol|diet|coffee)\b/i, "diet"],
  [/\b(form|note|letter|bill|billing|insurance)\b/i, "paperwork_billing"],
  [/\b(ice pack|brace|crutch|crutches|walker|reacher|bandage|supplies)\b/i, "supplies"],
];
const STOP = new Set([
  "a", "an", "the", "i", "my", "me", "can", "do", "does", "is", "it", "to", "for", "of", "and", "or", "when", "where",
  "what", "how", "should", "will", "be", "am", "are", "at", "on", "in", "after", "again", "get", "there", "anything",
  "allowed", "ok", "okay", "you", "your", "we", "this", "that", "with", "until", "long",
]);

function stem(w: string): string {
  let s = w;
  for (const suf of ["ing", "ed", "es", "s"]) {
    if (s.length > suf.length + 2 && s.endsWith(suf)) {
      s = s.slice(0, -suf.length);
      break;
    }
  }
  return s.length > 3 && s.endsWith("e") ? s.slice(0, -1) : s;
}

export function keywords(text: string): Set<string> {
  const out = new Set<string>();
  for (const w of text.toLowerCase().split(/[^a-z]+/)) {
    if (w && !STOP.has(w)) out.add(stem(w));
  }
  return out;
}

/** Share of the patient's keywords found in one text (0–1). */
export function keywordMatch(question: string, text: string): number {
  const q = keywords(question);
  if (!q.size) return 0;
  const p = keywords(text);
  let hit = 0;
  q.forEach((w) => { if (p.has(w)) hit += 1; });
  return hit / q.size;
}

function firstSentences(text: string, n: number): string {
  const parts = text.match(/[^.!?]+[.!?]+/g) ?? [text];
  return parts.slice(0, n).join(" ").replace(/\s+/g, " ").trim();
}

/**
 * The sample-data answer-or-defer, in the backend's order (classify, gate,
 * assemble, draft, verify). Keyword overlap stands in for the model and the
 * grounding check, so it only shows the shape of a result.
 */
export function demoTry(
  question: string,
  items: KnowledgeItem[],
  docs: KnowledgeDoc[],
  settings: KnowledgeSettings,
  context: KnowledgeTryContext = {},
): KnowledgeTryResult {
  const note = settings.mode === "live"
    ? "Sample data: a keyword match stands in for the real agent."
    : `Test only: mode is ${settings.mode}. Sample data: a keyword match stands in for the real agent.`;
  const q = question.trim();
  const latency_ms = 900 + (q.length * 37) % 1400;
  const base = { note, latency_ms, sources: [] as KnowledgeSource[], answer: null, support_score: null };
  if (!q) return { ...base, outcome: "deferred", reason: "empty_question", classification: null, latency_ms: 0 };

  const keyword_category = DEMO_MEDICATION.test(q) ? "medication" : DEMO_SYMPTOM.test(q) ? "symptom" : "other";
  const topic = DEMO_TOPICS.find(([re]) => re.test(q))?.[1] ?? null;
  const llm_category = keyword_category !== "other" && !topic ? keyword_category : topic;
  const red_flag = DEMO_RED_FLAG.test(q);
  const lockedKey = [keyword_category, llm_category].find((k) => k && settings.locked_categories.includes(k)) ?? null;
  const category = lockedKey ?? llm_category ?? "other";
  // A veto is one side blocking what the other would have let through.
  const kwLocked = settings.locked_categories.includes(keyword_category);
  const llmLocked = !!llm_category && settings.locked_categories.includes(llm_category);
  const vetoed_by: KnowledgeClassification["vetoed_by"] =
    red_flag || (kwLocked && !llmLocked) ? "keywords" : llmLocked && !kwLocked ? "llm" : null;
  const classification: KnowledgeClassification = {
    category, category_label: categoryLabelOf(category, settings.available_categories.length ? settings.available_categories : DEMO_CATEGORIES),
    llm_category, keyword_category, red_flag, vetoed_by,
  };
  const deferred = (reason: KnowledgeReason, extra: Partial<KnowledgeTryResult> = {}): KnowledgeTryResult =>
    ({ ...base, outcome: "deferred", reason, classification, ...extra });

  if (red_flag) return deferred("red_flag");
  if (lockedKey) return deferred("locked_category");
  if (!llm_category) return deferred("unclassified");
  if (!settings.allowed_categories.includes(llm_category)) return deferred("category_not_allowed");

  const material: { text: string; source: KnowledgeSource; answer: string }[] = [
    ...items.filter((i) => i.status === "approved").map((i) => ({
      text: [i.question, ...i.alt_questions].join(" "),
      answer: i.answer,
      source: { kind: "example" as const, id: i.item_id, title: i.question, version: i.version },
    })),
    ...docs.filter((d) => d.status === "approved" && docApplies(d, context)).map((d) => ({
      text: `${d.title} ${d.body}`,
      answer: firstSentences(d.body, 2),
      source: { kind: "guideline" as const, id: d.doc_id, title: d.title, version: d.version },
    })),
  ];
  if (!material.length) return deferred("no_knowledge");
  const scored = material.map((m) => ({ m, score: keywordMatch(q, m.text) })).sort((a, b) => b.score - a.score);
  const best = scored[0];
  if (best.score < 0.34) return deferred("not_covered");
  const support_score = Math.round(Math.min(0.99, 0.6 + best.score * 0.4) * 100) / 100;
  if (support_score < settings.min_support) {
    return deferred("low_support", { answer: best.m.answer, sources: [best.m.source], support_score });
  }
  return { ...base, outcome: "answered", reason: "answered", classification, answer: best.m.answer, sources: [best.m.source], support_score };
}

let demoSeq = 0;

/** A new sample entry (draft, version 1). */
export function demoCreate(input: KnowledgeItemInput, now = new Date()): KnowledgeItem {
  demoSeq += 1;
  const at = now.toISOString();
  return {
    item_id: `kq_demo_${now.getTime().toString(36)}_${demoSeq}`,
    question: input.question.trim(),
    alt_questions: input.alt_questions,
    answer: input.answer.trim(),
    category: input.category,
    category_label: categoryLabelOf(input.category),
    status: "draft",
    version: 1,
    created_at: at,
    created_by: "you (sample)",
    updated_at: at,
    updated_by: "you (sample)",
    approved_at: null,
    approved_by: null,
  };
}

/** The API's edit rule: an approved entry goes back to draft with version + 1. */
export function demoEdit(item: KnowledgeItem, input: KnowledgeItemInput, now = new Date()): KnowledgeItem {
  const wasApproved = item.status === "approved";
  return {
    ...item,
    question: input.question.trim(),
    alt_questions: input.alt_questions,
    answer: input.answer.trim(),
    category: input.category,
    category_label: categoryLabelOf(input.category),
    status: wasApproved ? "draft" : item.status,
    version: wasApproved ? item.version + 1 : item.version,
    updated_at: now.toISOString(),
    updated_by: "you (sample)",
  };
}
