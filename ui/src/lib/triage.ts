import { CallDoc } from "./types";
import { categoryLabel, questionSummary } from "./patient-questions";

/**
 * Turning a call into something a nurse can act on in a few seconds.
 *
 * The dashboard already stores what the patient said. Triage is the step
 * after that: which answers are *abnormal*, and does this call need a
 * human. Rendering raw key/value pairs makes the reader do that work; this
 * module does it once, so the UI can shout the exceptions and mute the rest.
 */

/** How much attention a single answer deserves. */
export type FindingLevel =
  | "alert"    // acute — act on this
  | "watch"    // notable, not acute
  | "normal"   // reassuring
  | "unknown"  // patient couldn't or wouldn't say
  | "unasked"; // question never reached (call ended early)

export interface Finding {
  key: string;
  label: string;
  /** Short enough to read at a glance. */
  value: string;
  /** Patient's own words, when they add colour the value can't. */
  detail?: string;
  level: FindingLevel;
  /**
   * The question did not exist when this call was made (questionnaire
   * changed 19 Oct 2026). Still shown as "Not asked", but not counted as
   * unanswered — an old all-clear call stays "Nothing abnormal".
   */
  predates?: boolean;
}

/** When the 19 Oct 2026 questions (legs, sleep, blood sugar reading, questions) went live. */
export const QUESTIONNAIRE_V2_FROM = "2026-10-19T00:00:00Z";
const V2_KEYS = ["legs_swollen", "swelling_description", "sleeping_well", "blood_sugar_reading", "has_questions", "patient_questions"];

/** An old-questionnaire call: made before the change, and carrying none of the new keys. */
export function predatesQuestionnaireV2(call: CallDoc): boolean {
  const r = (call.survey_responses ?? {}) as Record<string, unknown>;
  if (V2_KEYS.some((k) => r[k] !== undefined && r[k] !== null)) return false;
  const when = call.initiated_at ? Date.parse(call.initiated_at) : NaN;
  return Number.isFinite(when) && when < Date.parse(QUESTIONNAIRE_V2_FROM);
}

const s = (v: unknown): string | undefined => {
  if (v === undefined || v === null || v === "") return undefined;
  return String(v);
};

const n = (v: unknown): number | undefined => {
  const num = typeof v === "number" ? v : Number(v);
  return Number.isFinite(num) ? num : undefined;
};

/** Language that means "bad" even when no number was given. */
const SEVERE = /(terrible|awful|unbearable|excruciating|worst|severe|really bad|very bad)/i;
const NEGATIVE = /\b(poor|bad|rough|miserable|horrible|worse|struggling|not (?:good|great|well|ok|okay)|unwell|lousy|weak|exhausted)\b/i;
const POSITIVE = /\b(good|great|fine|ok|okay|well|better|excellent|fantastic|wonderful|alright|all right|no complaints)\b/i;

export function overallLevel(text: string): FindingLevel {
  if (SEVERE.test(text)) return "alert";
  if (NEGATIVE.test(text)) return "watch";
  if (POSITIVE.test(text)) return "normal";
  return "unknown";
}

/**
 * Ordered so the clinical answers come first — a nurse reads top-left to
 * bottom-right and should hit the acute stuff immediately. The patient's
 * questions for the care team come last (they also get their own section).
 */
export function findingsFor(call: CallDoc): Finding[] {
  const r = (call.survey_responses ?? {}) as Record<string, unknown>;
  const has = (k: string) => k in r && r[k] !== undefined && r[k] !== null && r[k] !== "";
  const out: Finding[] = [];
  const old = predatesQuestionnaireV2(call) || undefined;

  // --- Pain -------------------------------------------------------------
  const painPresent = s(r.pain_present)?.toLowerCase();
  if (!has("pain_present")) {
    out.push({ key: "pain", label: "Pain", value: "Not asked", level: "unasked" });
  } else if (painPresent === "no") {
    out.push({ key: "pain", label: "Pain", value: "None reported", level: "normal" });
  } else {
    const sev = n(r.pain_severity);
    const trend = s(r.pain_trend)?.toLowerCase();
    const note = s(r.pain_severity_note);
    const loc = s(r.pain_location);

    let level: FindingLevel = "watch";
    if (sev !== undefined) {
      if (sev >= 8) level = "alert";
      else if (sev >= 4) level = "watch";
      else level = "normal";
    } else if (note && SEVERE.test(note)) {
      level = "alert";
    } else if (note) {
      level = "unknown";
    }
    // A worsening trend outranks the raw number.
    if (trend === "worse" || trend === "worsening" || trend === "getting worse") {
      level = "alert";
    }

    const value = sev !== undefined ? `${sev} / 10` : note ? "No number given" : "Reported";
    // pain_location is now the patient's own description of the pain.
    const detail = [loc, trend && `${trend}`].filter(Boolean).join(" · ") || note;
    out.push({ key: "pain", label: "Pain", value, detail, level });
  }

  // --- Fever ------------------------------------------------------------
  const fever = s(r.fever_reported)?.toLowerCase();
  if (!has("fever_reported")) {
    out.push({ key: "fever", label: "Fever", value: "Not asked", level: "unasked" });
  } else if (fever === "yes") {
    out.push({ key: "fever", label: "Fever", value: "Yes — reported", level: "alert" });
  } else if (fever === "no") {
    out.push({ key: "fever", label: "Fever", value: "No", level: "normal" });
  } else {
    out.push({ key: "fever", label: "Fever", value: "Unsure", level: "unknown" });
  }

  // --- Incision ---------------------------------------------------------
  const inc = s(r.incision_concerns)?.toLowerCase();
  if (!has("incision_concerns")) {
    out.push({ key: "incision", label: "Incision", value: "Not asked", level: "unasked" });
  } else if (inc === "yes") {
    out.push({
      key: "incision",
      label: "Incision",
      value: "Concern reported",
      detail: s(r.incision_description),
      level: "alert",
    });
  } else if (inc === "no") {
    out.push({ key: "incision", label: "Incision", value: "Looks normal", level: "normal" });
  } else {
    out.push({ key: "incision", label: "Incision", value: "Unsure", level: "unknown" });
  }

  // --- Leg swelling (since 19 Oct 2026) ---------------------------------
  // One leg, worsening, painful, red or warm can be a clot: alert. Both
  // legs and stable is common after surgery: watch.
  const legs = s(r.legs_swollen)?.toLowerCase();
  if (!has("legs_swollen")) {
    out.push({ key: "legs", label: "Leg swelling", value: "Not asked", level: "unasked", predates: old });
  } else if (legs === "yes") {
    const desc = s(r.swelling_description);
    out.push({
      key: "legs",
      label: "Leg swelling",
      value: "Swelling reported",
      detail: desc,
      level: desc && swellingIsConcerning(desc) ? "alert" : "watch",
    });
  } else if (legs === "no") {
    out.push({ key: "legs", label: "Leg swelling", value: "No", level: "normal" });
  } else {
    out.push({ key: "legs", label: "Leg swelling", value: "Unsure", level: "unknown" });
  }

  // --- Sleep (since 19 Oct 2026) ----------------------------------------
  const sleep = s(r.sleeping_well)?.toLowerCase();
  if (!has("sleeping_well")) {
    out.push({ key: "sleep", label: "Sleep", value: "Not asked", level: "unasked", predates: old });
  } else if (sleep === "yes") {
    out.push({ key: "sleep", label: "Sleep", value: "Sleeping well", level: "normal" });
  } else if (sleep === "no") {
    out.push({ key: "sleep", label: "Sleep", value: "Not sleeping well", level: "watch" });
  } else {
    out.push({ key: "sleep", label: "Sleep", value: "Unsure", level: "unknown" });
  }

  // --- Blood sugar (diabetics only) -------------------------------------
  const ctx = (call.patient_context ?? {}) as Record<string, unknown>;
  if (ctx.is_diabetic === true || has("blood_sugar_status") || has("blood_sugar_reading")) {
    const bs = s(r.blood_sugar_status)?.toLowerCase();
    const reading = s(r.blood_sugar_reading);
    if (!has("blood_sugar_status")) {
      out.push(reading
        ? { key: "sugar", label: "Blood sugar", value: "Reading given", detail: reading, level: "unknown" }
        : { key: "sugar", label: "Blood sugar", value: "Not asked", level: "unasked" });
    } else if (bs === "in_range") {
      out.push({ key: "sugar", label: "Blood sugar", value: "In usual range", detail: reading, level: "normal" });
    } else if (bs === "high" || bs === "low") {
      out.push({
        key: "sugar",
        label: "Blood sugar",
        value: bs === "high" ? "Running high" : "Running low",
        detail: reading,
        level: "alert",
      });
    } else {
      out.push({ key: "sugar", label: "Blood sugar", value: "Unsure", detail: reading, level: "unknown" });
    }
  }

  // --- Identity ---------------------------------------------------------
  const dob = call.dob_verification_result?.toLowerCase();
  if (dob) {
    out.push({
      key: "dob",
      label: "Identity",
      value:
        dob === "verified" ? "DOB verified"
        : dob === "mismatch" ? "DOB did NOT match"
        : dob === "refused" ? "Declined to verify"
        : "Not verified",
      level: dob === "verified" ? "normal" : dob === "mismatch" ? "alert" : "watch",
    });
  }

  // --- Patient's own summary -------------------------------------------
  // Free text, so this is a colour, not a tier (the tier is `follow_up`).
  // It used to be green whatever the words were — "very poor" in the same
  // shade as "great". Negative words are a watch, the severe vocabulary an
  // alert, positive words normal, anything else neutral.
  const overall = s(r.overall_feeling);
  if (overall) {
    out.push({
      key: "overall",
      label: "In their words",
      value: overall.length > 90 ? overall.slice(0, 90) + "…" : overall,
      level: overallLevel(overall),
    });
  }

  // --- Questions for the care team (last: not a clinical answer) --------
  const q = questionSummary(call);
  if (q.count > 0 && q.forNurse === 0) {
    // Every question answered by the agent from approved clinic Q&A.
    out.push({
      key: "questions",
      label: "Questions for the care team",
      value: `${q.answered} answered by the agent`,
      detail: Array.from(new Set(q.questions.map((x) => categoryLabel(x.category)))).join(", "),
      level: "normal",
    });
  } else if (q.count > 0) {
    const cats = Array.from(new Set(q.questions.map((x) => categoryLabel(x.category))));
    out.push({
      key: "questions",
      label: "Questions for the care team",
      value:
        `${q.forNurse} ${q.forNurse === 1 ? "question" : "questions"}` +
        (q.immediate ? ` · ${q.immediate} immediate` : "") +
        (q.answered ? ` · ${q.answered} answered by the agent` : ""),
      detail: cats.join(", "),
      level: q.immediate ? "alert" : "watch",
    });
  } else if (q.noneCaptured) {
    out.push({
      key: "questions",
      label: "Questions for the care team",
      value: "Yes — none captured",
      detail: "Call to ask.",
      level: "watch",
    });
  } else if (q.hasQuestions === "no") {
    out.push({ key: "questions", label: "Questions for the care team", value: "None", level: "normal" });
  } else {
    out.push({ key: "questions", label: "Questions for the care team", value: "Not asked", level: "unasked", predates: old });
  }

  return out;
}

/** Negated phrases ("not getting worse", "no redness") must not trip the alert words. */
const NEGATED = /\b(?:not|no|isn'?t|aren'?t|hasn'?t|haven'?t|never|without|nor)\s+(?:any\s+|getting\s+|more\s+|really\s+|very\s+)*(?:worse|worsening|pain(?:ful)?|sore|tender|red(?:ness)?|warm(?:th)?|hot)\b/gi;
const SWELLING_ALERT = /\b(one leg|one foot|one ankle|one side|one calf|left|right|worse|worsening|pain|painful|sore|tender|hurts?|red|redness|warm|warmth|hot)\b/i;

/** One-sided, worsening, painful, red or warm swelling: possible clot. */
export function swellingIsConcerning(description: string): boolean {
  return SWELLING_ALERT.test(description.replace(NEGATED, " "));
}

/** Counts for the "N things need attention" summary line. */
export function countLevels(findings: Finding[]): Record<FindingLevel, number> {
  const c: Record<FindingLevel, number> = {
    alert: 0, watch: 0, normal: 0, unknown: 0, unasked: 0,
  };
  for (const f of findings) c[f.level] += 1;
  return c;
}

export interface Verdict {
  /** Headline a nurse reads first. */
  headline: string;
  /** One line of why. */
  reason: string;
  /** Drives colour. */
  tone: "emergency" | "urgent" | "routine" | "info" | "clear" | "incomplete";
  /** True when a human has to do something. */
  actionRequired: boolean;
}

/**
 * The single most important thing on the screen.
 *
 * Derived from follow_up (what the agent concluded) rather than re-deriving
 * it from findings — the agent had the conversation, and disagreeing with it
 * here would put two different answers on one screen.
 */
export function verdictFor(call: CallDoc): Verdict {
  const reason = call.follow_up_reason || "";

  // A call that never reached the questionnaire can't be cleared.
  const INCOMPLETE = new Set([
    "voicemail_left",
    "unable_to_reach_wrong_person",
    "reschedule_requested",
    "consent_declined",
  ]);
  if (call.outcome && INCOMPLETE.has(call.outcome)) {
    return {
      headline: "No answers collected",
      reason:
        reason ||
        {
          voicemail_left: "Voicemail left — patient not reached.",
          unable_to_reach_wrong_person: "Wrong number or unknown recipient.",
          reschedule_requested: "Patient asked to be called back later.",
          consent_declined: "Patient declined to continue.",
        }[call.outcome] ||
        "Call ended before the questions.",
      tone: "incomplete",
      actionRequired: false,
    };
  }

  switch (call.follow_up) {
    case "emergency":
      return {
        headline: "EMERGENCY",
        reason: reason || "Life-threatening symptom reported; 911 advised.",
        tone: "emergency",
        actionRequired: true,
      };
    case "urgent_same_day":
      return {
        headline: "Urgent — call back today",
        reason: reason || "Needs a same-day clinical callback.",
        tone: "urgent",
        actionRequired: true,
      };
    case "routine_next_business_day":
      return {
        headline: "Routine — next business day",
        reason: reason || "Nurse callback when convenient.",
        tone: "routine",
        actionRequired: true,
      };
    case "informational":
      return {
        headline: "No action required",
        reason: reason || "Noted for the record only.",
        tone: "info",
        actionRequired: false,
      };
    case "none":
      return {
        headline: "No action required",
        reason: reason || "No concerns reported.",
        tone: "clear",
        actionRequired: false,
      };
    default:
      // Still in flight, or ended without a report.
      return {
        headline: call.call_status ? "No follow-up recorded" : "Call in progress",
        reason: reason || (call.call_status
          ? "The call ended without a follow-up tier."
          : "Waiting for the call to complete."),
        tone: "incomplete",
        actionRequired: false,
      };
  }
}

export interface FindingSummary {
  text: string;
  tone: "alert" | "watch" | "muted" | "good";
}

/**
 * One line describing the findings grid.
 *
 * Deliberately never says "nothing abnormal" when questions went
 * unanswered. A call can carry `follow_up=urgent_same_day` with
 * `survey_responses: null` — the agent concluded urgency but submitted no
 * structured answers. Reporting that as "nothing abnormal" directly beneath
 * an URGENT banner is worse than saying nothing: it invites a nurse to
 * dismiss a call the agent escalated.
 */
export function summarize(findings: Finding[]): FindingSummary | null {
  const c = countLevels(findings.filter((f) => !f.predates));
  if (c.alert > 0) {
    return { text: `${c.alert} need${c.alert === 1 ? "s" : ""} attention`, tone: "alert" };
  }
  if (c.watch > 0) {
    return { text: `${c.watch} to note`, tone: "watch" };
  }
  const unanswered = c.unasked + c.unknown;
  if (unanswered > 0) {
    return { text: `${unanswered} not answered`, tone: "muted" };
  }
  if (c.normal > 0) {
    return { text: "Nothing abnormal", tone: "good" };
  }
  return null;
}
