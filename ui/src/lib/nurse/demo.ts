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

import { CallDoc, CallRun } from "../types";
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
      follow_up_reason: "Redness and warmth spreading around the incision; low-grade fever (100.6°F).",
      survey_responses: { pain_present: "yes", pain_severity: 5, pain_trend: "same", fever_reported: "yes",
        incision_concerns: "yes", incision_description: "red, warm, spreading", overall_feeling: "fair" },
    }),
    call({
      name: "Priya Raman", pid: "P-2043", op: "Rotator cuff repair", endedH: -27, mins: 5,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "urgent_same_day",
      follow_up_reason: "Pain uncontrolled on current regimen; out of prescribed medication.",
      survey_responses: { pain_present: "yes", pain_severity: 8, pain_trend: "same", fever_reported: "no",
        incision_concerns: "no", overall_feeling: "poor" },
    }),
    call({
      name: "Harold Okafor", pid: "P-2044", op: "Hip arthroplasty", endedH: -5, mins: 6,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "routine_next_business_day",
      follow_up_reason: "Blood sugar readings running high (210–240); diabetic.",
      survey_responses: { pain_present: "yes", pain_severity: 3, pain_trend: "better", fever_reported: "no",
        incision_concerns: "no", blood_sugar_status: "high", overall_feeling: "good" },
    }),
    call({
      name: "Lucia Fernández", pid: "P-2045", op: "Spinal fusion", endedH: -2, mins: 5,
      call_status: "success", outcome: "questionnaire_completed",
      follow_up: "routine_next_business_day",
      follow_up_reason: "Asked about resuming physical therapy and driving.",
      survey_responses: { pain_present: "yes", pain_severity: 4, pain_trend: "better", fever_reported: "no",
        incision_concerns: "no", overall_feeling: "good" },
    }),
    call({
      name: "Gregory Tan", pid: "P-2046", op: "Hernia repair", endedH: -0.6, mins: 4,
      call_status: "success", outcome: "questionnaire_completed", follow_up: "none",
      survey_responses: { pain_present: "no", fever_reported: "no", incision_concerns: "no", overall_feeling: "great" },
    }),
    call({
      name: "Angela Brooks", pid: "P-2047", op: "Total knee replacement", endedH: -4.2, mins: 0,
      channel: "sms", call_status: "success", outcome: "questionnaire_completed", follow_up: "none",
      survey_responses: { pain_present: "yes", pain_severity: 2, pain_trend: "better", fever_reported: "no",
        incision_concerns: "no", overall_feeling: "good" },
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
    entry("Jamal Whitaker", "P-2061", "Laminectomy", 3, 1, "calling"),
    entry("Eleanor Vance", "P-2060", "Total knee replacement", 2, 0),
    entry("Ingrid Solberg", "P-2062", "Mastectomy", 1, 0),
    entry("Abigail Porter", "P-2065", "Tonsillectomy", 1, 0),
    entry("Naomi Castillo", "P-2049", "Appendectomy", 2, 1),
    entry("Walter Kim", "P-2050", "Cataract surgery", 1, 1),
    entry("Felix Moreau", "P-2063", "Bunionectomy", 2, 0),
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
