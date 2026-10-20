/**
 * Admin → Knowledge base (docs/KNOWLEDGE-PLAN.md, steps 1–2): approved clinic
 * Q&A the voice agent may read to a patient word for word.
 *
 * Types follow the API (/api/admin/knowledge/*). The pure helpers here are
 * the panel's rules (filtering, what each status allows, draft validation)
 * and the sample-data "Try it", which only imitates the backend: the real
 * category gate and matching run on the server.
 */

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

export interface KnowledgeSettings {
  enabled: boolean;
  channels: { voice: boolean; sms: boolean };
  allowed_categories: string[];
  /** 0.5–1: how closely a patient's question must match an entry. */
  min_match: number;
  ask_satisfied: boolean;
  /** Read-only: never answered by the agent, whatever the settings. */
  locked_categories: string[];
  /** Read-only: every category an entry may have. */
  available_categories: KnowledgeCategory[];
}

/** What PUT /api/admin/knowledge/settings accepts (partial). */
export type KnowledgeSettingsPatch = Partial<
  Pick<KnowledgeSettings, "enabled" | "channels" | "allowed_categories" | "min_match" | "ask_satisfied">
>;

export interface KnowledgeItemInput {
  question: string;
  answer: string;
  category: string;
  alt_questions: string[];
}

export interface KnowledgeTryResult {
  outcome: "answered" | "deferred";
  category: string;
  category_label: string;
  reason: string;
  answer?: string;
  item_id?: string;
  item_version?: number;
  confidence?: number;
  note?: string;
}

// ---- Limits (the API enforces the same) ----------------------------------

export const MAX_QUESTION = 300;
export const MAX_ANSWER = 600;
export const MAX_ALT_QUESTIONS = 10;
export const MIN_MATCH_RANGE = { min: 0.5, max: 1, step: 0.05 } as const;

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

export function statusCounts(items: KnowledgeItem[]): Record<StatusFilter, number> {
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

export function clampMinMatch(v: number): number {
  if (!Number.isFinite(v)) return MIN_MATCH_RANGE.min;
  return Math.round(Math.min(MIN_MATCH_RANGE.max, Math.max(MIN_MATCH_RANGE.min, v)) * 100) / 100;
}

/** Only the fields that changed, for the partial PUT. */
export function settingsPatch(saved: KnowledgeSettings, draft: KnowledgeSettings): KnowledgeSettingsPatch {
  const p: KnowledgeSettingsPatch = {};
  if (draft.enabled !== saved.enabled) p.enabled = draft.enabled;
  if (draft.channels.voice !== saved.channels.voice || draft.channels.sms !== saved.channels.sms) p.channels = { ...draft.channels };
  const a = [...draft.allowed_categories].sort().join(",");
  const b = [...saved.allowed_categories].sort().join(",");
  if (a !== b) p.allowed_categories = [...draft.allowed_categories];
  if (clampMinMatch(draft.min_match) !== clampMinMatch(saved.min_match)) p.min_match = clampMinMatch(draft.min_match);
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
    enabled: false,
    channels: { voice: true, sms: false },
    allowed_categories: ["appointment", "activity", "diet", "paperwork_billing", "supplies", "recovery"],
    min_match: 0.8,
    ask_satisfied: true,
    locked_categories: [...LOCKED_CATEGORIES],
    available_categories: DEMO_CATEGORIES,
  };
}

export function categoryLabelOf(key: string, categories: KnowledgeCategory[] = DEMO_CATEGORIES): string {
  return categories.find((c) => c.key === key)?.label ?? key.replace(/_/g, " ");
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

// The sample "Try it": keyword rules, nothing like the real classifier.
const DEMO_MEDICATION = /\b(pill|pills|ibuprofen|tylenol|aspirin|medication|medicine|meds|dose|antibiotic|blood thinner|prescription|refill)\b/i;
const DEMO_SYMPTOM = /\b(pain|hurt|hurts|fever|bleed|bleeding|swollen|swelling|red|redness|pus|drainage|wound|incision|stitches|dizzy|nausea)\b/i;
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

/** Share of the patient's keywords found in one phrasing (0–1). */
export function keywordMatch(question: string, phrasing: string): number {
  const q = keywords(question);
  if (!q.size) return 0;
  const p = keywords(phrasing);
  let hit = 0;
  q.forEach((w) => { if (p.has(w)) hit += 1; });
  return hit / q.size;
}

/** The sample-data answer-or-defer, following the backend's order: gate, then match. */
export function demoTry(question: string, items: KnowledgeItem[], settings: KnowledgeSettings): KnowledgeTryResult {
  const note = settings.enabled
    ? undefined
    : "Answering is off: on a call the agent would defer every question. This shows what it would do with it on.";
  if (DEMO_MEDICATION.test(question)) {
    return { outcome: "deferred", category: "medication", category_label: categoryLabelOf("medication"),
      reason: "Medication questions are never answered by the agent. A nurse calls back the same day.", note };
  }
  if (DEMO_SYMPTOM.test(question)) {
    return { outcome: "deferred", category: "symptom", category_label: categoryLabelOf("symptom"),
      reason: "Symptom and wound questions are never answered by the agent. A nurse calls back the same day.", note };
  }
  let best: { item: KnowledgeItem; score: number } | null = null;
  for (const it of items) {
    if (it.status !== "approved") continue;
    for (const p of [it.question, ...it.alt_questions]) {
      const score = keywordMatch(question, p);
      if (!best || score > best.score) best = { item: it, score };
    }
  }
  if (!best || best.score < settings.min_match) {
    return { outcome: "deferred", category: best && best.score > 0 ? best.item.category : "other",
      category_label: best && best.score > 0 ? best.item.category_label : "Other",
      reason: best && best.score > 0
        ? `The closest approved entry matched ${Math.round(best.score * 100)}%, below the minimum of ${Math.round(settings.min_match * 100)}%.`
        : "No approved entry matches this question.",
      confidence: best ? best.score : undefined, note };
  }
  const { item, score } = best;
  if (!settings.allowed_categories.includes(item.category)) {
    return { outcome: "deferred", category: item.category, category_label: item.category_label,
      reason: `${item.category_label} questions are switched off in the settings.`, note };
  }
  return { outcome: "answered", category: item.category, category_label: item.category_label,
    reason: "Matched an approved clinic answer.", answer: item.answer, item_id: item.item_id,
    item_version: item.version, confidence: score, note };
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
