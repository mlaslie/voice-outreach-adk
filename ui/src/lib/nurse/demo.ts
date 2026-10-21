/**
 * Fictional data for designing the nurse console without a backend.
 *
 * Every name and number is invented. Times are generated relative to "now"
 * so the schedule always has a Today and a Tomorrow, and the follow-up list
 * always has something overdue.
 *
 * The shapes are the real API shapes, so the demo goes through the same
 * view-model code as live data (lib/nurse/model.ts).
 */

import { AllowlistEntry, AllowlistResponse, CallDoc, CallRun } from "../types";
import { FollowUpSession, ScheduledCallback } from "../followups";
import { PlannedEntry } from "./model";
import { DEFAULT_CALLING_HOURS, DEFAULT_CLINIC_TZ, nextCallingSlot } from "./window";

export interface NurseData {
  entries: PlannedEntry[];
  runs: CallRun[];
  calls: CallDoc[];
  sessions: FollowUpSession[];
  callbacks: ScheduledCallback[];
}

export function demoData(now: Date = new Date()): NurseData {
  const at = (hoursFromNow: number) => new Date(now.getTime() + hoursFromNow * 3_600_000).toISOString();
  // Anything scheduled in the future lands inside the default calling hours,
  // the same rule the proxy enforces on real runs and bookings.
  const inHours = (iso: string) =>
    nextCallingSlot(new Date(iso).getTime(), DEFAULT_CALLING_HOURS, DEFAULT_CLINIC_TZ) ?? iso;
  const atClock = (dayOffset: number, h: number, m = 0) => {
    const d = new Date(now);
    d.setDate(d.getDate() + dayOffset);
    d.setHours(h, m, 0, 0);
    return d.toISOString();
  };
  let n = 0;
  const phone = () => `+120255501${String(++n % 100).padStart(2, "0")}`;

  const call = (p: Partial<CallDoc> & { name: string; pid: string; op: string; endedH: number; mins?: number }): CallDoc => {
    const ended = at(p.endedH);
    const started = new Date(new Date(ended).getTime() - (p.mins ?? 4) * 60_000).toISOString();
    const sid = `CA${(p.pid + "0".repeat(32)).slice(0, 32).toLowerCase().replace(/[^0-9a-f]/g, "a")}`;
    return {
      doc_id: sid,
      call_sid: sid,
      to: p.to ?? phone(),
      from: "+12025550100",
      initiated_at: started,
      updated_at: ended,
      answered_at: started,
      ended_at: ended,
      duration_seconds: Math.round((p.mins ?? 4) * 60),
      patient_id: p.pid,
      patient_name: p.name,
      patient_context: { operation_name: p.op },
      ...p,
    } as CallDoc;
  };

  // Dolores Whitfield and Angela Brooks keep the pre-19-Oct-2026 answer
  // shape (no leg, sleep or questions keys), as old calls do in production.
  // Dolores also has no follow_up_reasons list (only the sentence), as calls
  // before the reasons list do; Harold's reasons were changed by a nurse.
  const calls: CallDoc[] = [
    call({
      name: "Dolores Whitfield", pid: "P-2041", op: "Total knee replacement", endedH: -1.2, mins: 7,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "emergency",
      follow_up_reason: "Chest tightness and shortness of breath since this morning; advised to call 911.",
      survey_responses: { pain_present: "yes", pain_severity: 9, pain_trend: "worse", pain_location: "chest",
        fever_reported: "no", incision_concerns: "no", overall_feeling: "very poor" },
    }),
    call({
      name: "Marcus Ellery", pid: "P-2042", op: "Laparoscopic cholecystectomy", endedH: -3.5, mins: 6,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "urgent_same_day",
      follow_up_reason: "Patient reports incision concern (red, warm, spreading), fever. Also noted: has questions for the care team (next business day); not sleeping well (noted).",
      follow_up_reasons: [
        { tier: "urgent_same_day", text: "incision concern (red, warm, spreading)", source: "triage", fields: ["incision_concerns", "incision_description"] },
        { tier: "urgent_same_day", text: "fever", source: "triage", fields: ["fever_reported"] },
        { tier: "routine_next_business_day", text: "has questions for the care team", source: "triage", fields: ["has_questions"] },
        { tier: "informational", text: "not sleeping well", source: "triage", fields: ["sleeping_well"] },
      ],
      survey_responses: { pain_present: "yes", pain_severity: 5, pain_trend: "same",
        pain_location: "a sharp pull around the incision when I sit up", fever_reported: "yes",
        incision_concerns: "yes", incision_description: "red, warm, spreading", overall_feeling: "fair",
        legs_swollen: "no", sleeping_well: "no", has_questions: "yes", patient_questions: [] },
    }),
    call({
      name: "Priya Raman", pid: "P-2043", op: "Rotator cuff repair", endedH: -27, mins: 5,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "urgent_same_day",
      follow_up_reason: "Patient reports question asked (medication), pain 8/10. Also noted: not sleeping well (noted).",
      follow_up_reasons: [
        { tier: "urgent_same_day", text: "question asked (medication)", source: "triage", fields: ["patient_questions"] },
        { tier: "urgent_same_day", text: "pain 8/10", source: "triage", fields: ["pain_severity"] },
        { tier: "informational", text: "not sleeping well", source: "triage", fields: ["sleeping_well"] },
      ],
      survey_responses: { pain_present: "yes", pain_severity: 8, pain_trend: "same",
        pain_location: "deep ache in the shoulder, worse at night", fever_reported: "no",
        incision_concerns: "no", legs_swollen: "no", sleeping_well: "no", overall_feeling: "poor",
        has_questions: "yes",
        patient_questions: [
          { text: "I'm almost out of the pain pills. Can I take ibuprofen with them, or should I stop one?",
            category: "medication", priority: "urgent_same_day",
            // Shadow mode (docs/KNOWLEDGE-PLAN.md §4): what the agent would have done, rated by a nurse.
            shadow: { outcome: "deferred", reason: "locked_category", at: at(-0.9) },
            shadow_review: { verdict: "good", note: null, by: "J. Moreno, RN", at: at(-0.5) } },
        ] },
    }),
    call({
      name: "Harold Okafor", pid: "P-2044", op: "Hip arthroplasty", endedH: -5, mins: 6,
      patient_context: { operation_name: "Hip arthroplasty", is_diabetic: true },
      call_status: "success", outcome: "questionnaire_completed",
      // A nurse raised this one after reading the answers; the agent's own
      // assessment is kept alongside.
      follow_up: "urgent_same_day",
      follow_up_reason: "Nurse: Blood sugar out of range; Medication question.",
      follow_up_reasons: [
        { tier: "urgent_same_day", text: "Blood sugar out of range", source: "nurse", set_by: "J. Moreno, RN", at: at(-4) },
        { tier: "urgent_same_day", text: "Medication question", source: "nurse", set_by: "J. Moreno, RN", at: at(-4) },
      ],
      agent_follow_up: "routine_next_business_day",
      agent_follow_up_reason: "Patient reports blood sugar high (between 210 and 240 most mornings).",
      agent_follow_up_reasons: [
        { tier: "routine_next_business_day", text: "blood sugar high (between 210 and 240 most mornings)", source: "triage", fields: ["blood_sugar_status", "blood_sugar_reading"] },
      ],
      follow_up_override: {
        at: at(-4), from: "routine_next_business_day", to: "urgent_same_day", set_by: "J. Moreno, RN",
        reason: "Readings this high on insulin need a same-day dose review.",
        reasons: ["Blood sugar out of range", "Medication question"],
      },
      follow_up_history: [{
        at: at(-4), from: "routine_next_business_day", to: "urgent_same_day", set_by: "J. Moreno, RN",
        reason: "Readings this high on insulin need a same-day dose review.",
        reasons: ["Blood sugar out of range", "Medication question"],
      }],
      survey_responses: { pain_present: "yes", pain_severity: 3, pain_trend: "better",
        pain_location: "sore along the outside of the hip", fever_reported: "no",
        incision_concerns: "no", legs_swollen: "no", sleeping_well: "yes",
        blood_sugar_status: "high", blood_sugar_reading: "between 210 and 240 most mornings",
        overall_feeling: "good", has_questions: "no" },
    }),
    call({
      name: "Lucia Fernández", pid: "P-2045", op: "Spinal fusion", endedH: -2, mins: 5,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "routine_next_business_day",
      follow_up_reason: "Patient reports question asked (appointment), question asked (activity).",
      follow_up_reasons: [
        { tier: "routine_next_business_day", text: "question asked (appointment)", source: "triage", fields: ["patient_questions"] },
        { tier: "routine_next_business_day", text: "question asked (activity)", source: "triage", fields: ["patient_questions"] },
      ],
      survey_responses: { pain_present: "yes", pain_severity: 4, pain_trend: "better",
        pain_location: "stiff in the lower back, easing each day", fever_reported: "no",
        incision_concerns: "no", legs_swollen: "no", sleeping_well: "yes", overall_feeling: "good",
        has_questions: "yes",
        patient_questions: [
          { text: "When is my follow-up appointment with the surgeon? Nobody has called me yet.",
            category: "appointment", priority: "routine_next_business_day",
            shadow: { outcome: "deferred", reason: "not_covered", at: at(-1.9) } },
          { text: "When am I allowed to drive again?", category: "activity", priority: "routine_next_business_day",
            shadow: {
              outcome: "answered", reason: "answered", at: at(-1.9), support_score: 0.92,
              answer: "Please don't drive while you are taking prescription pain medicine, and not for at least two weeks after surgery. Your surgeon will confirm at your follow-up visit.",
              sources: [{ kind: "example", id: "kq_drive", title: "When can I drive again?", version: 1 }],
            } },
        ] },
    }),
    call({
      name: "Gregory Tan", pid: "P-2046", op: "Hernia repair", endedH: -0.6, mins: 4,
      call_status: "success", outcome: "questionnaire_completed", follow_up: "none",
      survey_responses: { pain_present: "no", fever_reported: "no", incision_concerns: "no", legs_swollen: "no",
        sleeping_well: "yes", overall_feeling: "great", has_questions: "no" },
    }),
    call({
      name: "Angela Brooks", pid: "P-2047", op: "Total knee replacement", endedH: -4.2, mins: 0,
      channel: "sms", call_status: "success", outcome: "questionnaire_completed", follow_up: "none",
      survey_responses: { pain_present: "yes", pain_severity: 2, pain_trend: "better", fever_reported: "no",
        incision_concerns: "no", overall_feeling: "good" },
    }),
    call({
      name: "Vivian Ashcombe", pid: "P-2054", op: "Total knee replacement", endedH: -0.9, mins: 6,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "urgent_same_day",
      follow_up_reason: "Patient reports swelling in one leg, getting worse.",
      follow_up_reasons: [
        { tier: "urgent_same_day", text: "swelling in one leg, getting worse", source: "triage", fields: ["legs_swollen", "swelling_description"] },
      ],
      survey_responses: { pain_present: "yes", pain_severity: 5, pain_trend: "same",
        pain_location: "tight, aching calf below the new knee", fever_reported: "no", incision_concerns: "no",
        legs_swollen: "yes", swelling_description: "Just the left leg. The calf is puffier than yesterday and tender.",
        sleeping_well: "yes", overall_feeling: "okay", has_questions: "no" },
    }),
    call({
      name: "Samuel Reyes", pid: "P-2048", op: "Carpal tunnel release", endedH: -6, mins: 1,
      call_status: "left_vmail", outcome: "voicemail_left",
    }),
    call({
      name: "Naomi Castillo", pid: "P-2049", op: "Appendectomy", endedH: -2.6, mins: 0.5,
      call_status: "rna",
    }),
    call({
      name: "Walter Kim", pid: "P-2050", op: "Cataract surgery", endedH: -8, mins: 0,
      call_status: "busy",
    }),
    call({
      name: "Beatrice Lowell", pid: "P-2051", op: "Hip arthroplasty", endedH: -1.8, mins: 2,
      call_status: "interrupted",
    }),
    call({
      name: "Theo Nakamura", pid: "P-2052", op: "ACL reconstruction", endedH: -3, mins: 2,
      call_status: "success", outcome: "reschedule_requested",
      follow_up_reason: "At work; asked to be called back after 6 pm.",
    }),
    call({
      name: "Rosa Delgado", pid: "P-2053", op: "Thyroidectomy", endedH: -7, mins: 1,
      call_status: "left_vmail", outcome: "voicemail_left",
    }),
  ];

  const byName = (name: string) => calls.find((c) => c.patient_name === name)!;
  const reyes = byName("Samuel Reyes");
  const castillo = byName("Naomi Castillo");
  const delgado = byName("Rosa Delgado");
  const brooks = byName("Angela Brooks");

  const sessions: FollowUpSession[] = [
    { phone: reyes.to, call_sid: reyes.call_sid, patient_name: reyes.patient_name, patient_id: reyes.patient_id,
      state: "scheduled", scheduled_for: inHours(at(2.5)), scheduled_for_text: "later this afternoon",
      last_invite_at: at(-5.9), last_inbound_at: at(-5.5), message_count: 4 },
    { phone: castillo.to, call_sid: castillo.call_sid, patient_name: castillo.patient_name, patient_id: castillo.patient_id,
      state: "awaiting_choice", last_invite_at: at(-2.5), message_count: 1 },
    { phone: delgado.to, call_sid: delgado.call_sid, patient_name: delgado.patient_name, patient_id: delgado.patient_id,
      state: "scheduled", scheduled_for: atClock(1, 9, 0), scheduled_for_text: "tomorrow morning at 9",
      last_invite_at: at(-6.9), last_inbound_at: at(-6.2), message_count: 5 },
    { phone: brooks.to, call_sid: brooks.call_sid, patient_name: brooks.patient_name, patient_id: brooks.patient_id,
      state: "closed", last_invite_at: at(-5), last_inbound_at: at(-4.2), message_count: 12 },
  ];

  const callbacks: ScheduledCallback[] = [
    { callback_id: "cb-1", phone: reyes.to, run_at: inHours(at(2.5)), status: "scheduled",
      patient_name: reyes.patient_name, patient_id: reyes.patient_id, origin_call_sid: reyes.call_sid },
    { callback_id: "cb-2", phone: delgado.to, run_at: atClock(1, 9, 0), status: "scheduled",
      patient_name: delgado.patient_name, patient_id: delgado.patient_id, origin_call_sid: delgado.call_sid },
  ];

  const entry = (name: string, pid: string, op: string, opDaysAgo: number, attempts: number,
    status: PlannedEntry["list_status"] = "pending"): PlannedEntry => ({
    entry_id: pid.toLowerCase().replace(/[^a-z0-9]/g, "") + "0".repeat(24),
    phone: phone(),
    patient_name: name,
    patient_id: pid,
    patient_dob: "1958-04-12",
    operation_name: op,
    operation_date: new Date(now.getTime() - opDaysAgo * 86_400_000).toISOString().slice(0, 10),
    is_diabetic: false,
    list_status: status,
    attempts,
    created_at: at(-30),
    updated_at: at(-1),
  });

  const entries: PlannedEntry[] = [
    { ...entry("Jamal Whitaker", "P-2061", "Laminectomy", 3, 1, "calling"), care_setting: "inpatient" },
    { ...entry("Eleanor Vance", "P-2060", "Total knee replacement", 2, 0), care_setting: "inpatient" },
    entry("Ingrid Solberg", "P-2062", "Mastectomy", 1, 0),
    entry("Abigail Porter", "P-2065", "Tonsillectomy", 1, 0),
    entry("Naomi Castillo", "P-2049", "Appendectomy", 2, 1),
    entry("Walter Kim", "P-2050", "Cataract surgery", 1, 1),
    { ...entry("Felix Moreau", "P-2063", "Bunionectomy", 2, 0), care_setting: "outpatient" },
    entry("Chen Wei", "P-2064", "Sinus surgery", 1, 0),
    // On the list but not in any run yet — shows under Settings only.
    entry("Odette Marsh", "P-2066", "Shoulder arthroscopy", 1, 0),
    // Cancelled from the queue this morning — on Call status, reschedulable.
    {
      ...entry("Desmond Hale", "P-2067", "Inguinal hernia repair", 2, 0, "canceled"),
      canceled: {
        at: at(-2.2),
        reason: "Readmitted overnight for observation; surgeon asked to hold follow-up calls.",
        by: "charge.rn",
        run_scheduled_for: at(1.5),
      },
    },
  ];

  // Run times are relative to now, so the queue never shows a run the
  // scheduler missed, whatever the clock says when the demo is opened — and
  // each is moved into the default calling hours (inHours, above).
  const id = (p: string) => p.toLowerCase().replace(/[^a-z0-9]/g, "") + "0".repeat(24);
  let r = 0;
  const run = (when: string, pids: string[], label: string | null, status: CallRun["status"] = "scheduled"): CallRun => ({
    run_id: String(++r).padStart(32, "0"),
    scheduled_for: when,
    entry_ids: pids.map(id),
    label,
    status,
    created_at: at(-20),
    updated_at: at(-1),
    ...(status === "dispatched" ? { dispatched_at: when, dialled: pids.length } : {}),
  });
  const runs: CallRun[] = [
    run(at(-0.05), ["P-2061"], "Post-op day 3", "dispatched"),
    run(inHours(at(0.75)), ["P-2060", "P-2062", "P-2065"], "Post-op day 2"),
    run(inHours(at(3.5)), ["P-2049"], "Retry"),
    run(inHours(atClock(1, 11, 0)), ["P-2050", "P-2063"], "Next morning"),
    run(inHours(atClock(2, 10, 30)), ["P-2064"], null),
  ];

  return { entries, runs, calls, sessions, callbacks };
}

/**
 * A fictional allowlist for the admin console's sample data: the demo
 * patients' invented numbers, about half of them with text consent, plus one
 * deployment-level (env) entry that cannot be edited.
 */
export function demoAllowlist(entries: PlannedEntry[]): {
  allowlist: AllowlistResponse;
  consent: Record<string, boolean>;
} {
  const phones = [...new Set(entries.map((e) => e.phone))];
  const consent: Record<string, boolean> = {};
  const list: AllowlistEntry[] = phones.map((phone, i) => {
    consent[phone] = i % 2 === 0;
    const name = entries.find((e) => e.phone === phone)?.patient_name ?? "Test patient";
    return {
      phone,
      label: `${name} (sample) - ${consent[phone] ? "sms consent" : "no sms consent"}`,
      source: "firestore",
      editable: true,
    };
  });
  list.push({ phone: "+12025550199", label: "Clinic test line (sample)", source: "env", editable: false });
  // One sample patient is left off the list so the "not an approved number" state shows.
  const shown = list.length > 2 ? list.filter((_, i) => i !== 1) : list;
  return { allowlist: { entries: shown, count: shown.length, mode: "list", unrestricted: false }, consent };
}
