/**
 * The nurse Analytics tab's data (docs/ANALYTICS-PLAN.md, phase 1).
 *
 * The private voa-analytics service computes these numbers from de-identified
 * BigQuery tables; the nurse UI's route /api/analytics/summary forwards to it.
 * Nothing here carries a name or number: the UI resolves names itself from the
 * call list (entry_id) and the calls it already holds (call_id).
 */

export type Channel = "all" | "voice" | "sms";
export type TierKey = "emergency" | "urgent_same_day" | "routine_next_business_day" | "informational" | "none";

export const TIER_ORDER: TierKey[] = ["emergency", "urgent_same_day", "routine_next_business_day", "informational", "none"];
export const TIER_LABEL: Record<TierKey, string> = {
  emergency: "Emergency",
  urgent_same_day: "Same day",
  routine_next_business_day: "Next business day",
  informational: "Informational",
  none: "No follow-up",
};

export interface Unreached {
  patient_key: string | null;
  entry_id: string | null;
  attempts: number;
  last_call_id: string | null;
  last_at: string | null;
}

export interface AnalyticsSummary {
  refreshed_at?: string | null;
  range: { start_day: string; end_day: string; channel: Channel; calls: number };
  outreach: {
    patients_attempted: number;
    patients_reached: number;
    reach_rate: number | null;
    voice_funnel: { dialled: number; connected: number; human: number; verified: number; completed: number };
    text_funnel: { invites_sent: number; completed_by_text: number };
    attempts_per_reached: number | null;
    unreached: Unreached[];
  };
  clinical: {
    tiers_by_day: ({ day: string } & Partial<Record<TierKey, number>>)[];
    top_reasons: { text: string; tier: TierKey; count: number }[];
    questions: { by_category: Record<string, number>; by_priority: Record<string, number> };
    symptoms: Record<string, number>;
    emergencies: string[];
  };
  workload: {
    open_by_tier: Partial<Record<TierKey, number>>;
    /** Open past their callback window. */
    overdue_by_tier?: Partial<Record<TierKey, number>>;
    signoff_minutes: Partial<Record<TierKey, { median: number | null; p90: number | null; n: number }>>;
    override: { rate: number | null; up: number; down: number; n: number };
  };
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  return `${Math.round(value * 100)}%`;
}

/** `part` of `whole` as a 0–100 bar width; 0 when there is nothing to divide. */
export function share(part: number, whole: number): number {
  if (!whole || whole <= 0) return 0;
  return Math.max(0, Math.min(100, (part / whole) * 100));
}

export function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  if (value < 60) return `${Math.round(value)} min`;
  const h = value / 60;
  return h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)} h` : `${Math.round(h / 24)} d`;
}

/** Symptom keys from the service, in display order with their labels. */
export const SYMPTOMS: [string, string][] = [
  ["fever_yes", "Fever"],
  ["incision_yes", "Incision concern"],
  ["swelling_yes", "Swelling"],
  ["swelling_urgent", "Swelling, urgent"],
  ["poor_sleep", "Not sleeping well"],
  ["sugar_high", "Blood sugar high"],
  ["sugar_low", "Blood sugar low"],
];
export const PAIN_BUCKETS: [string, string][] = [
  ["pain_0", "0"],
  ["pain_1_3", "1–3"],
  ["pain_4_7", "4–7"],
  ["pain_8_10", "8–10"],
];

export function summaryUrl(days: number, channel: Channel): string {
  const d = Math.max(1, Math.min(365, Math.round(days)));
  return `/api/analytics/summary?days=${d}&channel=${channel}`;
}

export async function fetchSummary(days: number, channel: Channel, signal?: AbortSignal): Promise<AnalyticsSummary> {
  const r = await fetch(summaryUrl(days, channel), { cache: "no-store", signal });
  if (!r.ok) {
    let detail = `Analytics is unavailable (${r.status}).`;
    try {
      const j = await r.json();
      if (typeof j?.detail === "string") detail = j.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(detail);
  }
  return (await r.json()) as AnalyticsSummary;
}

/** Fictional numbers for /nurse?data=demo. */
export function sampleSummary(days: number, channel: Channel, today = new Date()): AnalyticsSummary {
  const dayList = Array.from({ length: Math.min(days, 14) }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (Math.min(days, 14) - 1 - i));
    return d.toISOString().slice(0, 10);
  });
  return {
    refreshed_at: today.toISOString(),
    range: { start_day: dayList[0], end_day: dayList.at(-1)!, channel, calls: 212 },
    outreach: {
      patients_attempted: 96,
      patients_reached: 81,
      reach_rate: 81 / 96,
      voice_funnel: { dialled: 188, connected: 121, human: 104, verified: 97, completed: 88 },
      text_funnel: { invites_sent: 34, completed_by_text: 11 },
      attempts_per_reached: 1.7,
      unreached: [],
    },
    clinical: {
      tiers_by_day: dayList.map((day, i) => ({
        day,
        emergency: i % 6 === 2 ? 1 : 0,
        urgent_same_day: (i * 3) % 4,
        routine_next_business_day: 2 + (i % 3),
        informational: 1 + (i % 2),
        none: 4 + (i % 4),
      })),
      top_reasons: [
        { text: "Medication question", tier: "urgent_same_day", count: 14 },
        { text: "Pain 7 or more", tier: "urgent_same_day", count: 9 },
        { text: "Not sleeping well", tier: "routine_next_business_day", count: 8 },
        { text: "Swelling in legs or feet", tier: "routine_next_business_day", count: 6 },
        { text: "Incision redness or drainage", tier: "urgent_same_day", count: 4 },
        { text: "Fever", tier: "emergency", count: 2 },
      ],
      questions: {
        by_category: { medication: 14, appointment: 9, activity: 6, diet: 3, other: 2 },
        by_priority: { urgent_same_day: 16, routine_next_business_day: 18 },
      },
      symptoms: {
        fever_yes: 2, incision_yes: 4, swelling_yes: 7, swelling_urgent: 1, poor_sleep: 8,
        sugar_high: 2, sugar_low: 0, pain_0: 12, pain_1_3: 31, pain_4_7: 36, pain_8_10: 9,
      },
      emergencies: [],
    },
    workload: {
      open_by_tier: { emergency: 0, urgent_same_day: 3, routine_next_business_day: 7 },
      signoff_minutes: {
        emergency: { median: 6, p90: 11, n: 2 },
        urgent_same_day: { median: 74, p90: 190, n: 27 },
        routine_next_business_day: { median: 820, p90: 1460, n: 31 },
      },
      override: { rate: 0.08, up: 2, down: 3, n: 60 },
    },
  };
}
