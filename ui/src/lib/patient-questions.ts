import {
  CallDoc, KnowledgeSource, PatientQuestion, QuestionCategory, QuestionPriority, ShadowDraft, ShadowReview,
} from "./types";

/**
 * Questions the patient asked the care team during the questionnaire
 * (19 Oct 2026 survey change). Each one needs a nurse to call back; the
 * backend's triage already folds them into `follow_up`. This module only
 * reads them out of `survey_responses` safely — old calls have no such
 * keys, and anything malformed is dropped rather than rendered as
 * "[object Object]".
 */

export const CATEGORY_LABEL: Record<QuestionCategory, string> = {
  medication: "Medication",
  symptom: "Symptom or wound",
  appointment: "Appointment",
  activity: "Activity",
  diet: "Diet",
  paperwork_billing: "Paperwork or billing",
  supplies: "Supplies",
  recovery: "Recovery timeline",
  other: "Other",
};

export const PRIORITY_LABEL: Record<QuestionPriority, string> = {
  urgent_same_day: "Immediate",
  routine_next_business_day: "Next business day",
};

/** Nurse-console tone for each priority (see components/nurse/parts TONE). */
export const PRIORITY_TONE: Record<QuestionPriority, "urgent" | "routine"> = {
  urgent_same_day: "urgent",
  routine_next_business_day: "routine",
};

export function categoryLabel(category: string | undefined): string {
  if (!category) return CATEGORY_LABEL.other;
  return CATEGORY_LABEL[category as QuestionCategory] ?? category.replace(/_/g, " ");
}

/**
 * Anything the backend did not mark routine is treated as immediate: an
 * unrecognised or missing priority must never be quietly downgraded.
 */
function normalisePriority(p: unknown): QuestionPriority {
  return p === "routine_next_business_day" ? "routine_next_business_day" : "urgent_same_day";
}

function rank(p: QuestionPriority): number {
  return p === "urgent_same_day" ? 0 : 1;
}

/** Immediate first; otherwise the order the patient asked them in. */
export function sortQuestions(qs: PatientQuestion[]): PatientQuestion[] {
  return qs
    .map((q, i) => ({ q, i }))
    .sort((a, b) => rank(a.q.priority) - rank(b.q.priority) || a.i - b.i)
    .map(({ q }) => q);
}

/** A validated question and its position in survey_responses.patient_questions (the API's {index}). */
export interface QuestionEntry {
  q: PatientQuestion;
  index: number;
}

function score(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function sources(v: unknown): KnowledgeSource[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out: KnowledgeSource[] = [];
  for (const x of v as unknown[]) {
    if (!x || typeof x !== "object") continue;
    const r = x as Record<string, unknown>;
    const id = typeof r.id === "string" ? r.id : "";
    const title = typeof r.title === "string" ? r.title : "";
    if (!id && !title) continue;
    const src: KnowledgeSource = { kind: r.kind === "guideline" ? "guideline" : "example", id, title };
    if (typeof r.version === "number" && Number.isFinite(r.version)) src.version = r.version;
    out.push(src);
  }
  return out;
}

function shadowDraft(v: unknown): ShadowDraft | undefined {
  if (!v || typeof v !== "object") return undefined;
  const r = v as Record<string, unknown>;
  if (r.outcome !== "answered" && r.outcome !== "deferred") return undefined;
  const d: ShadowDraft = { outcome: r.outcome, reason: typeof r.reason === "string" ? r.reason : "" };
  if (typeof r.answer === "string" && r.answer.trim()) d.answer = r.answer.trim();
  const src = sources(r.sources);
  if (src) d.sources = src;
  const sc = score(r.support_score);
  if (sc !== undefined) d.support_score = sc;
  if (typeof r.at === "string") d.at = r.at;
  return d;
}

function shadowReview(v: unknown): ShadowReview | undefined {
  if (!v || typeof v !== "object") return undefined;
  const r = v as Record<string, unknown>;
  if (r.verdict !== "good" && r.verdict !== "not_right") return undefined;
  return {
    verdict: r.verdict,
    note: typeof r.note === "string" && r.note.trim() ? r.note.trim() : null,
    by: typeof r.by === "string" ? r.by : null,
    at: typeof r.at === "string" ? r.at : null,
  };
}

/** The call's questions with their positions, validated and sorted. Empty for old calls. */
export function questionEntries(call: Pick<CallDoc, "survey_responses">): QuestionEntry[] {
  const raw: unknown = call.survey_responses?.patient_questions;
  if (!Array.isArray(raw)) return [];
  const out: QuestionEntry[] = [];
  (raw as unknown[]).forEach((item, index) => {
    if (!item || typeof item !== "object") return;
    const r = item as Record<string, unknown>;
    const text = typeof r.text === "string" ? r.text.trim() : "";
    if (!text) return;
    const q: PatientQuestion = {
      text,
      category: (typeof r.category === "string" && r.category ? r.category : "other") as QuestionCategory,
      priority: normalisePriority(r.priority),
    };
    // Knowledge-base fields: copied only when present and well formed, so an
    // older question keeps exactly its three fields.
    if (r.outcome === "answered" || r.outcome === "deferred") q.outcome = r.outcome;
    if (typeof r.item_id === "string" && r.item_id) q.item_id = r.item_id;
    if (typeof r.item_version === "number" && Number.isFinite(r.item_version)) q.item_version = r.item_version;
    if (typeof r.answer === "string" && r.answer.trim()) q.answer = r.answer.trim();
    if (r.satisfied === true || r.satisfied === false || r.satisfied === null) q.satisfied = r.satisfied;
    const src = sources(r.sources);
    if (src) q.sources = src;
    const sc = score(r.support_score);
    if (sc !== undefined) q.support_score = sc;
    const sh = shadowDraft(r.shadow);
    if (sh) q.shadow = sh;
    const rv = shadowReview(r.shadow_review);
    if (rv) q.shadow_review = rv;
    out.push({ q, index });
  });
  return out
    .map((e, i) => ({ e, i }))
    .sort((a, b) => rank(a.e.q.priority) - rank(b.e.q.priority) || a.i - b.i)
    .map(({ e }) => e);
}

/** The call's questions, validated and sorted. Empty for old calls. */
export function questionsFor(call: Pick<CallDoc, "survey_responses">): PatientQuestion[] {
  return questionEntries(call).map((e) => e.q);
}

// ---- Shadow drafts (docs/KNOWLEDGE-PLAN.md §4) -----------------------------

export const MAX_REVIEW_NOTE = 300;

export const VERDICT_LABEL: Record<ShadowReview["verdict"], string> = { good: "Good", not_right: "Not right" };

/**
 * The review the rating route sent back. The plan does not fix the response
 * shape, so this accepts the review itself, an object carrying
 * `shadow_review`, or the updated call (then the question at `index`), and
 * otherwise falls back to what was sent.
 */
export function reviewFromResponse(res: unknown, index: number, fallback: ShadowReview): ShadowReview {
  const direct = shadowReview(res);
  if (direct) return direct;
  if (res && typeof res === "object") {
    const r = res as Record<string, unknown>;
    const nested = shadowReview(r.shadow_review);
    if (nested) return nested;
    const q = shadowReview((r.question as Record<string, unknown> | undefined)?.shadow_review);
    if (q) return q;
    const qs = (r.survey_responses as Record<string, unknown> | undefined)?.patient_questions;
    if (Array.isArray(qs)) {
      const fromCall = shadowReview((qs[index] as Record<string, unknown> | undefined)?.shadow_review);
      if (fromCall) return fromCall;
    }
  }
  return fallback;
}

/** The call with one question's shadow_review replaced (for showing a rating at once). */
export function withShadowReview(call: CallDoc, index: number, review: ShadowReview): CallDoc {
  const qs = call.survey_responses?.patient_questions;
  if (!Array.isArray(qs) || !qs[index]) return call;
  const next = qs.map((q, i) => (i === index ? { ...q, shadow_review: review } : q));
  return { ...call, survey_responses: { ...call.survey_responses, patient_questions: next } };
}

export interface ShadowStats {
  /** Questions with a shadow draft. */
  drafts: number;
  /** Drafts a nurse rated. */
  rated: number;
  good: number;
  notRight: number;
}

/** Shadow drafts and their ratings across calls (for the go-live warning). */
export function shadowStats(calls: Pick<CallDoc, "survey_responses">[]): ShadowStats {
  const out: ShadowStats = { drafts: 0, rated: 0, good: 0, notRight: 0 };
  for (const c of calls) {
    for (const q of questionsFor(c)) {
      if (!q.shadow) continue;
      out.drafts += 1;
      if (!q.shadow_review) continue;
      out.rated += 1;
      if (q.shadow_review.verdict === "good") out.good += 1;
      else out.notRight += 1;
    }
  }
  return out;
}

/**
 * Who dealt with a question (docs/KNOWLEDGE-PLAN.md):
 *   - "answered":    the agent read an approved clinic answer and the patient
 *                    did not say it missed;
 *   - "unsatisfied": the agent answered, but the patient said it didn't
 *                    answer their question, so it is a nurse's again;
 *   - "nurse":       deferred, or an older question without the fields.
 */
export type QuestionHandling = "answered" | "unsatisfied" | "nurse";

export function questionHandling(q: Pick<PatientQuestion, "outcome" | "satisfied">): QuestionHandling {
  if (q.outcome !== "answered") return "nurse";
  return q.satisfied === false ? "unsatisfied" : "answered";
}

/** "Clinic Q&A v3", or "Clinic Q&A" when the version was not recorded. */
export function answerSource(q: Pick<PatientQuestion, "item_version">): string {
  return typeof q.item_version === "number" ? `Clinic Q&A v${q.item_version}` : "Clinic Q&A";
}

export interface QuestionSummary {
  /** has_questions as the patient answered it, or undefined if never asked. */
  hasQuestions?: "yes" | "no";
  questions: PatientQuestion[];
  count: number;
  immediate: number;
  /** Answered by the agent, and the patient did not say it missed. */
  answered: number;
  /** count - answered: the questions a nurse still has to deal with. */
  forNurse: number;
  /** Patient said yes but nothing was captured — a nurse must ask. */
  noneCaptured: boolean;
}

export function questionSummary(call: Pick<CallDoc, "survey_responses">): QuestionSummary {
  const r = (call.survey_responses ?? {}) as Record<string, unknown>;
  const raw = typeof r.has_questions === "string" ? r.has_questions.toLowerCase() : undefined;
  const questions = questionsFor(call);
  const hasQuestions: "yes" | "no" | undefined =
    questions.length ? "yes" : raw === "yes" ? "yes" : raw === "no" ? "no" : undefined;
  const immediate = questions.filter((q) => q.priority === "urgent_same_day").length;
  const answered = questions.filter((q) => questionHandling(q) === "answered").length;
  return {
    hasQuestions,
    questions,
    count: questions.length,
    immediate,
    answered,
    forNurse: questions.length - answered,
    noneCaptured: hasQuestions === "yes" && questions.length === 0,
  };
}

export interface QuestionPill {
  label: string;
  /** "good" only when the agent answered every question. */
  tone: "urgent" | "routine" | "good";
  /** Hover text: the questions themselves. */
  title: string;
}

/** The small "N questions" pill for call lists, or null when there is nothing to flag. */
export function questionPill(call: Pick<CallDoc, "survey_responses">): QuestionPill | null {
  const s = questionSummary(call);
  if (s.noneCaptured) {
    return { label: "Has questions", tone: "routine", title: "Patient has questions — none captured; call to ask." };
  }
  if (!s.count) return null;
  const title = s.questions
    .map((q) => `${PRIORITY_LABEL[q.priority]} · ${categoryLabel(q.category)}: ${q.text}${HANDLING_SUFFIX[questionHandling(q)]}`)
    .join("\n");
  if (s.forNurse === 0) {
    return { label: `${s.answered} answered`, tone: "good", title };
  }
  return {
    label: `${s.forNurse} ${s.forNurse === 1 ? "question" : "questions"}`,
    tone: s.immediate > 0 ? "urgent" : "routine",
    title,
  };
}

const HANDLING_SUFFIX: Record<QuestionHandling, string> = {
  answered: " (answered by the agent)",
  unsatisfied: " (answered by the agent; patient said it didn't answer it)",
  nurse: "",
};

/** One line per question for plain key/value lists. */
export function formatQuestion(q: PatientQuestion): string {
  return `${q.text} (${categoryLabel(q.category)}, ${PRIORITY_LABEL[q.priority]})${HANDLING_SUFFIX[questionHandling(q)]}`;
}
