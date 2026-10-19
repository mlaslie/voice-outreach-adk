import { CallDoc, PatientQuestion, QuestionCategory, QuestionPriority } from "./types";

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

/** The call's questions, validated and sorted. Empty for old calls. */
export function questionsFor(call: Pick<CallDoc, "survey_responses">): PatientQuestion[] {
  const raw: unknown = call.survey_responses?.patient_questions;
  if (!Array.isArray(raw)) return [];
  const out: PatientQuestion[] = [];
  for (const item of raw as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    const text = typeof r.text === "string" ? r.text.trim() : "";
    if (!text) continue;
    out.push({
      text,
      category: (typeof r.category === "string" && r.category ? r.category : "other") as QuestionCategory,
      priority: normalisePriority(r.priority),
    });
  }
  return sortQuestions(out);
}

export interface QuestionSummary {
  /** has_questions as the patient answered it, or undefined if never asked. */
  hasQuestions?: "yes" | "no";
  questions: PatientQuestion[];
  count: number;
  immediate: number;
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
  return {
    hasQuestions,
    questions,
    count: questions.length,
    immediate,
    noneCaptured: hasQuestions === "yes" && questions.length === 0,
  };
}

export interface QuestionPill {
  label: string;
  tone: "urgent" | "routine";
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
  return {
    label: `${s.count} ${s.count === 1 ? "question" : "questions"}`,
    tone: s.immediate > 0 ? "urgent" : "routine",
    title: s.questions
      .map((q) => `${PRIORITY_LABEL[q.priority]} · ${categoryLabel(q.category)}: ${q.text}`)
      .join("\n"),
  };
}

/** One line per question for plain key/value lists. */
export function formatQuestion(q: PatientQuestion): string {
  return `${q.text} (${categoryLabel(q.category)}, ${PRIORITY_LABEL[q.priority]})`;
}
