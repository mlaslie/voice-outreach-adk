import { CallDoc } from "./types";

export const TERMINAL = new Set([
  "success",
  "left_vmail",
  "rna",
  "busy",
  "interrupted",
  "failed",
  "canceled",
]);

export const isActive = (c: CallDoc): boolean =>
  !c.call_status || !TERMINAL.has(c.call_status);

// Chips use tints of the brand palette (app/theme.css) rather than stock Tailwind
// hues, so status colour reads as part of the same system as the buttons.
export const STATUS_CHIP_CLASSES: Record<string, string> = {
  success: "bg-brand/10 text-brand-dark",
  left_vmail: "bg-brand-blue/10 text-brand-blue-dark",
  rna: "bg-mist text-ink-soft",
  busy: "bg-brand-orange/25 text-ink",
  failed: "bg-brand-orange/25 text-ink",
  interrupted: "bg-brand-coral/15 text-brand-coral-dark",
  canceled: "bg-mist text-ink-soft",
  in_flight: "bg-brand-turquoise/25 text-brand-deep",
};

export const STATUS_LABELS: Record<string, string> = {
  success: "Success",
  left_vmail: "Left voicemail",
  rna: "Rang — no answer",
  busy: "Busy",
  failed: "Failed",
  interrupted: "Interrupted",
  canceled: "Canceled",
  in_flight: "In flight",
};

export function getStatusLabel(callStatus?: string): string {
  if (!callStatus) return STATUS_LABELS.in_flight;
  return STATUS_LABELS[callStatus] ?? callStatus;
}

export function getStatusClasses(callStatus?: string): string {
  if (!callStatus) return STATUS_CHIP_CLASSES.in_flight;
  return STATUS_CHIP_CLASSES[callStatus] ?? "bg-mist text-ink";
}

// ---- Follow-up tiers --------------------------------------------------------
//
// The agent reports one of: emergency | urgent_same_day |
// routine_next_business_day | informational | none. The first three mean a
// human has to call the patient back, so those are what the banner counts.
// "informational" is logged but needs no callback.

export const FOLLOW_UP_TIERS = [
  "emergency",
  "urgent_same_day",
  "routine_next_business_day",
] as const;

export type FollowUpTier = (typeof FOLLOW_UP_TIERS)[number];

/** Most-severe first — the banner takes its styling from the worst present. */
export const FOLLOW_UP_SEVERITY: FollowUpTier[] = [
  "emergency",
  "urgent_same_day",
  "routine_next_business_day",
];

export const FOLLOW_UP_LABELS: Record<string, string> = {
  emergency: "Emergency",
  urgent_same_day: "Urgent — same day",
  routine_next_business_day: "Routine — next business day",
  informational: "Informational",
  none: "None",
};

export const FOLLOW_UP_CHIP_CLASSES: Record<string, string> = {
  emergency: "bg-brand-red/10 text-brand-red-dark",
  urgent_same_day: "bg-brand-coral/15 text-brand-coral-dark",
  routine_next_business_day: "bg-brand-yellow/30 text-ink",
  informational: "bg-mist-cool text-ink",
  none: "bg-mist text-ink-soft",
};

export function getFollowUpLabel(tier?: string): string {
  if (!tier) return "—";
  return FOLLOW_UP_LABELS[tier] ?? tier;
}

export function getFollowUpChipClasses(tier?: string): string {
  if (!tier) return "bg-mist text-ink-soft";
  return FOLLOW_UP_CHIP_CLASSES[tier] ?? "bg-mist text-ink-soft";
}

/** True when this call needs a human callback. */
export function needsFollowUp(c: CallDoc): boolean {
  return !!c.follow_up && (FOLLOW_UP_TIERS as readonly string[]).includes(c.follow_up);
}

/** One patient: by MRN, else by phone among calls with no MRN. Same rule as
 *  the proxy's _same_patient and the GE agent's patient_key (B-6, 18 Oct). */
export function patientKey(c: CallDoc): string {
  if (c.patient_id) return `pid:${c.patient_id}`;
  return c.to ? `to:${c.to}` : `doc:${c.doc_id}`;
}

/** One call per patient — the most recent attempt. */
export function latestCallPerPatient(calls: CallDoc[]): CallDoc[] {
  const latest = new Map<string, CallDoc>();
  for (const c of calls) {
    const key = patientKey(c);
    const prev = latest.get(key);
    if (
      !prev ||
      new Date(c.initiated_at).getTime() > new Date(prev.initiated_at).getTime()
    ) {
      latest.set(key, c);
    }
  }
  return [...latest.values()];
}

/**
 * One call per patient — the most recent call that was ASSESSED, i.e. that
 * carries a `follow_up` verdict (from the agent, the SMS questionnaire, or a
 * nurse's override).
 *
 * This is what follow-up status must use, not latestCallPerPatient. A redial
 * that came back clean clears an earlier flag; a redial that never reached
 * the patient (voicemail, no answer, dropped) says nothing about them and
 * must NOT. Using the latest call of any kind did exactly that: a patient
 * flagged urgent by text at 11:57 vanished from every follow-up count when
 * the 1:50 scheduled call went to voicemail (16 Oct 2026).
 */
export function latestAssessedCallPerPatient(calls: CallDoc[]): CallDoc[] {
  return latestCallPerPatient(calls.filter(isAssessment));
}

/**
 * Does this call say anything about the patient's condition? A raised flag
 * always does; a nurse's tier change does; but "none" / "informational"
 * counts only on a completed questionnaire. The voice agent files
 * follow_up="none" on a VOICEMAIL ("no patient contact"), and that must never
 * clear an urgent flag from an earlier call (seen live, 16 Oct 2026).
 */
export function isAssessment(c: CallDoc): boolean {
  return needsFollowUp(c) || !!c.follow_up_override ||
    (!!c.follow_up && c.outcome === "questionnaire_completed");
}

/** A nurse signed it off (for a follow-up, resolved it), or it was closed as
 *  superseded when a later report for the same patient was signed off. */
export function isSignedOff(c: CallDoc): boolean {
  return c.review?.status === "signed_off" || c.review?.status === "superseded";
}

/**
 * The calls the follow-up banner counts and the follow-up filter shows: one
 * per patient, their most severe open report (newest first on a tie).
 *
 * A report that needed a callback stays open until a nurse closes it (signs
 * off / resolves it, here or in Gemini Enterprise). A later clean report
 * does NOT close it (user decision D-1, 18 Oct): a patient flagged urgent who
 * later texts "feeling fine" still needs the urgent report resolved.
 * Voicemails are not assessments and never open or close anything.
 */
export function followUpCalls(calls: CallDoc[]): CallDoc[] {
  const rank = (c: CallDoc) => (FOLLOW_UP_TIERS as readonly string[]).indexOf(c.follow_up as string);
  const best = new Map<string, CallDoc>();
  for (const c of calls) {
    if (!needsFollowUp(c) || isSignedOff(c)) continue;
    const k = patientKey(c);
    const prev = best.get(k);
    if (!prev || rank(c) < rank(prev) ||
        (rank(c) === rank(prev) && new Date(c.initiated_at).getTime() > new Date(prev.initiated_at).getTime())) {
      best.set(k, c);
    }
  }
  return [...best.values()];
}

export interface FollowUpSummary {
  /** Number of distinct patients needing a callback. */
  total: number;
  byTier: Record<FollowUpTier, number>;
  /** Most severe tier present, or null when nothing needs follow-up. */
  worst: FollowUpTier | null;
}

export function summarizeFollowUps(calls: CallDoc[]): FollowUpSummary {
  const byTier = {
    emergency: 0,
    urgent_same_day: 0,
    routine_next_business_day: 0,
  } as Record<FollowUpTier, number>;

  const outstanding = followUpCalls(calls);
  for (const c of outstanding) {
    byTier[c.follow_up as FollowUpTier] += 1;
  }

  const worst = FOLLOW_UP_SEVERITY.find((t) => byTier[t] > 0) ?? null;
  return { total: outstanding.length, byTier, worst };
}

export const DOB_CHIP_CLASSES: Record<string, string> = {
  verified: "bg-brand/10 text-brand-dark",
  mismatch: "bg-brand-pink/15 text-brand-pink-dark",
  refused: "bg-brand-orange/25 text-ink",
  skipped: "bg-mist text-ink-soft",
};

export function getDobChipClasses(result?: string): string {
  if (!result) return "bg-mist text-ink";
  const key = result.toLowerCase();
  return DOB_CHIP_CLASSES[key] ?? "bg-mist text-ink";
}
