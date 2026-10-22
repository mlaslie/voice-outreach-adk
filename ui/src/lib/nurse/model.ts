/**
 * View models for the nurse console (/nurse).
 *
 * The console shows three worklists — who is due a call, what happened on
 * the calls already made, and who needs a human callback. The backend
 * stores those facts in four shapes (call-list entries, calls, SMS
 * sessions, scheduled callbacks); this module folds them into one row type
 * per list, once, so the screens only render.
 *
 * Pure and dependency-free so it can be tested, and so the demo dataset
 * and live data go through exactly the same code.
 */

import { CallDoc, CallListEntry, CallRun, CancelEvent } from "../types";
import { FollowUpSession, ScheduledCallback } from "../followups";
import { followUpCalls, isActive, latestAssessedCallPerPatient, needsFollowUp, patientKey } from "../status";
import { callLanguage } from "../languages";

// ---------------------------------------------------------------------------
// Schedule
// ---------------------------------------------------------------------------

/**
 * Why this call is on the queue.
 *
 * first_call        never attempted
 * retry             attempted before, still pending (no answer, voicemail…)
 * text_reschedule   the patient booked a time by text
 *
 * The queue shows SCHEDULED calls only: the patients in each call run
 * (created under Settings) and the callbacks patients booked by text.
 */
export type ScheduleOrigin = "first_call" | "retry" | "text_reschedule";

export type ScheduleState =
  | "upcoming"
  | "overdue"    // the run's time passed and the scheduler has not dialled — it may be down
  | "calling"
  | "queued"     // in a run that is being dialled now, waiting for its turn; can still be cancelled
  | "attempted"  // dialled by the run; not reached — the text follow-up takes over
  | "done"       // dialled by the run and the questionnaire was completed
  | "blocked"    // the proxy refused the number (not on the approved list)
  | "held";      // the patient's language is not available yet: not called (docs/LANGUAGES-PLAN.md)

export interface ScheduleItem {
  id: string;
  /** The call-list entry to dial for "Call now" (absent for text bookings). */
  entryId?: string;
  /** The call run this patient belongs to (absent for text bookings). */
  runId?: string;
  patientName: string;
  patientId?: string;
  phone: string;
  procedure?: string;
  /** ISO time the call will be (or was) placed. */
  when: string;
  /** The patient's own words for the time, when they booked by text. */
  whenText?: string | null;
  origin: ScheduleOrigin;
  state: ScheduleState;
  /** Previous attempts. 0 for a first call. */
  attempts: number;
  /** Outcome of the last attempt (our vocabulary: rna, left_vmail, busy…). */
  lastCallStatus?: string;
  /** The call that led to this booking, if any. */
  sourceCallSid?: string | null;
  /** The patient's preferred language (absent = English (US)). */
  language?: string;
  /** Why the patient is held for their language, when state is "held". */
  held?: string;
}

/** Why a call-list patient is held for their language, or null (lib/languages holdReason). */
export type HoldOf = (e: PlannedEntry) => string | null;

export type PlannedEntry = CallListEntry;

/**
 * The queue is what is still to come. A dialled run leaves it — its outcomes
 * are on the Call status board, and anyone it missed comes back as a NEW
 * Auto-recall run at a future time — except the patients still on the phone.
 */
export function runOnQueue(run: CallRun, now: Date = new Date()): boolean {
  if (run.status !== "dispatched") return true;
  return startOfDay(new Date(run.scheduled_for)) === startOfDay(now);
}

/** Grace before a run whose time has passed counts as missed by the scheduler. */
const SCHEDULER_GRACE_MS = 3 * 60_000;

export function scheduleItems(
  entries: PlannedEntry[],
  runs: CallRun[],
  callbacks: ScheduledCallback[],
  sessions: FollowUpSession[],
  calls: CallDoc[],
  now: Date = new Date(),
  holdOf: HoldOf = () => null,
): ScheduleItem[] {
  const out: ScheduleItem[] = [];
  const sessionByCall = new Map(
    sessions.filter((s) => s.call_sid).map((s) => [s.call_sid as string, s]),
  );
  const entryById = new Map(entries.map((e) => [e.entry_id, e]));
  // A patient who booked a time by text is not also shown in an Auto-recall
  // run (the proxy drops them from it on its next tick; 19 Oct).
  const textBooked = new Set(
    callbacks.filter((cb) => cb.status === "scheduled" || cb.status === "dialing").map((cb) => cb.phone));

  for (const run of runs) {
    if (!runOnQueue(run, now)) continue;
    const when = run.scheduled_for;
    for (const eid of Array.isArray(run.entry_ids) ? run.entry_ids : []) {
      const e = entryById.get(eid);
      if (!e) continue; // removed from the list; the backend drops it from the run too
      if (run.auto_recall && run.status === "scheduled" && textBooked.has(e.phone)) continue;
      // Once the run has dialled, its own attempt is already counted.
      const prior = run.status === "dispatched" ? Math.max(0, e.attempts - 1) : e.attempts;
      // A patient held for their language is never dialled: say so, in a run
      // still to come and in today's run that passed them by.
      const held = e.list_status === "pending" ? holdOf(e) : null;
      const state = held ? "held" : memberState(run, e, now);
      // Already called or already reached: not on the queue (never a past time).
      if (state === "attempted" || state === "done") continue;
      if (run.status === "dispatched" && state !== "calling" && state !== "queued" && state !== "held") continue;
      out.push({
        id: `run:${run.run_id}:${eid}`,
        entryId: eid,
        runId: run.run_id,
        patientName: e.patient_name,
        patientId: e.patient_id,
        phone: e.phone,
        procedure: e.operation_name,
        when,
        origin: prior > 0 ? "retry" : "first_call",
        state,
        attempts: prior,
        lastCallStatus: e.last_call_status,
        sourceCallSid: e.last_call_sid ?? null,
        language: e.language || undefined,
        held: held ?? undefined,
      });
    }
  }

  const callBySid = new Map(calls.map((c) => [c.call_sid ?? c.doc_id, c]));
  for (const cb of callbacks) {
    if (cb.status !== "scheduled" && cb.status !== "dialing") continue;
    const session = cb.origin_call_sid ? sessionByCall.get(cb.origin_call_sid) : undefined;
    const origin = cb.origin_call_sid ? callBySid.get(cb.origin_call_sid) : undefined;
    out.push({
      id: `callback:${cb.callback_id}`,
      patientName: cb.patient_name ?? "Unknown patient",
      patientId: cb.patient_id,
      phone: cb.phone,
      procedure: origin ? procedureOf(origin) : undefined,
      when: cb.run_at,
      whenText: session?.scheduled_for_text ?? null,
      origin: "text_reschedule",
      state: cb.status === "dialing" ? "calling"
        : new Date(cb.run_at).getTime() + SCHEDULER_GRACE_MS < now.getTime() ? "overdue" : "upcoming",
      attempts: 1,
      sourceCallSid: cb.origin_call_sid ?? null,
      language: origin ? callLanguage(origin) : undefined,
    });
  }

  return out.sort(compareSchedule);
}

function memberState(run: CallRun, e: PlannedEntry, now: Date): ScheduleState {
  if (e.list_status === "calling") return "calling";
  if (run.status === "dispatching" || run.status === "dispatched") {
    if (e.list_status === "completed" || e.list_status === "canceled") return "done";
    if (e.list_status === "blocked") return "blocked";
    // The runner dials a run's members one at a time, so a run flips to
    // "dispatched" long before its last patient is rung. A member not
    // attempted since the run started is still waiting its turn — and a
    // nurse can still cancel it (the dialler re-checks, backlog B-2).
    const started = run.dispatched_at ?? run.claimed_at ?? run.scheduled_for;
    const tried = e.last_attempt_at && new Date(e.last_attempt_at).getTime() >= new Date(started).getTime();
    // Once the run has finished nobody in it is waiting; anyone it did not
    // reach was carried into a new run or skipped (review of B-2).
    return tried || run.finished_at ? "attempted" : "queued";
  }
  // Scheduled. A patient already reached, or cancelled, is not called by the
  // run (the backend also takes a cancelled patient out of it).
  if (e.list_status === "completed" || e.list_status === "canceled") return "done";
  return new Date(run.scheduled_for).getTime() + SCHEDULER_GRACE_MS < now.getTime() ? "overdue" : "upcoming";
}

/** In progress first, then anything the scheduler missed, then by time. */
function compareSchedule(a: ScheduleItem, b: ScheduleItem): number {
  const RANK: Record<ScheduleState, number> = {
    calling: 0, queued: 1, overdue: 1, upcoming: 2, attempted: 2, done: 2, blocked: 2, held: 2,
  };
  const rank = (s: ScheduleItem) => RANK[s.state];
  if (rank(a) !== rank(b)) return rank(a) - rank(b);
  return a.when.localeCompare(b.when) || a.patientName.localeCompare(b.patientName);
}

/** "Today", "Tomorrow", a weekday, or a date — the schedule's group headings. */
export function dayHeading(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const days = Math.round((startOfDay(d) - startOfDay(now)) / 86_400_000);
  if (days < 0) return "Overdue";
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

// ---------------------------------------------------------------------------
// Completed
// ---------------------------------------------------------------------------

export type CompletedKind =
  | "completed_phone"
  | "completed_text"
  | "voicemail"
  | "no_answer"
  | "unreachable" // busy, failed, cancelled
  | "interrupted"
  | "reschedule"   // reached, but asked to be called back another time
  | "wrong_person"
  | "declined"
  | "canceled";    // a nurse cancelled the queued call before it was placed

export interface CompletedItem {
  id: string;
  patientName: string;
  patientId?: string;
  phone: string;
  procedure?: string;
  /** When the call ended (or started, if it never connected). */
  at: string;
  kind: CompletedKind;
  durationSec?: number;
  /** A follow-up text went out after this call. */
  textSent: boolean;
  /** Why this call's follow-up text was not sent (no consent, the other system has the number, ...). */
  textSkipped?: string;
  /** The patient wrote back. */
  textReplied: boolean;
  optedOut: boolean;
  followUp?: string;
  /** A callback the patient booked by text off this call, still pending. */
  callback?: ScheduledCallback;
  /** A nurse changed the tier (the agent's own verdict is in `call.agent_follow_up`). */
  overridden: boolean;
  /**
   * This call's tier is the patient's current status: their open report in
   * the follow-up list (D-1: open until a nurse closes it), or, for "no
   * follow-up", their latest assessment while nothing earlier is open. Keeps
   * the board's filters in step with the Follow-ups list and the KPI tile.
   */
  current: boolean;
  /** Absent for a cancelled queued call: no phone call was ever placed. */
  call?: CallDoc;
  /** Set for a cancelled queued call: the call-list entry, and why. */
  entry?: CallListEntry;
  cancel?: CancelEvent;
  /** The patient's preferred language (absent = English (US)). */
  language?: string;
}

export function completedItems(
  calls: CallDoc[],
  sessions: FollowUpSession[],
  callbacks: ScheduledCallback[] = [],
  entries: CallListEntry[] = [],
): CompletedItem[] {
  const sessionByCall = new Map(
    sessions.filter((s) => s.call_sid).map((s) => [s.call_sid as string, s]),
  );
  const pending = callbacks.filter((cb) => cb.status === "scheduled" || cb.status === "dialing");
  // "Current" = what this call says about the patient NOW, the same rule as
  // the follow-up list (D-1): a patient's row in followUpCalls for the
  // needs-a-callback filters; for "no follow-up", their latest assessment,
  // and only while nothing earlier is still open.
  const open = followUpCalls(calls);
  const openIds = new Set(open.map((c) => c.doc_id));
  const openPatients = new Set(open.map(patientKey));
  const latestIds = new Set(latestAssessedCallPerPatient(calls).map((c) => c.doc_id));
  const isCurrent = (c: CallDoc) =>
    needsFollowUp(c) ? openIds.has(c.doc_id) : latestIds.has(c.doc_id) && !openPatients.has(patientKey(c));
  const out: CompletedItem[] = [];
  for (const c of calls) {
    if (isActive(c)) continue;
    const sid = c.call_sid ?? c.doc_id;
    const session = sessionByCall.get(sid);
    const callback =
      pending.find((cb) => cb.origin_call_sid === sid) ??
      // The booking names the handset, not always the call; fall back to
      // the phone only when nothing on this list claims the call.
      pending.find((cb) => cb.phone === c.to && !cb.origin_call_sid);
    out.push({
      id: c.doc_id,
      patientName: c.patient_name ?? "Unknown patient",
      patientId: c.patient_id,
      phone: c.to,
      procedure: procedureOf(c),
      at: c.ended_at ?? c.updated_at ?? c.initiated_at,
      kind: kindOf(c),
      durationSec: c.duration_seconds,
      textSent: c.sms_follow_up?.status === "sent" || (!c.sms_follow_up && !!session?.last_invite_at),
      textSkipped: c.sms_follow_up?.status === "not_sent" ? c.sms_follow_up.reason || "Not sent." : undefined,
      textReplied: !!session?.last_inbound_at,
      optedOut: session?.state === "opted_out",
      followUp: c.follow_up,
      callback,
      overridden: !!c.follow_up_override,
      current: isCurrent(c),
      call: c,
      language: callLanguage(c),
    });
  }
  // A call a nurse cancelled from the queue never rang, so it has no call
  // doc; it is the call-list entry, with the reason, until it is rescheduled.
  for (const e of entries) {
    if (e.list_status !== "canceled" || !e.canceled) continue;
    out.push({
      id: `cancel:${e.entry_id}`,
      patientName: e.patient_name,
      patientId: e.patient_id,
      phone: e.phone,
      procedure: e.operation_name || undefined,
      at: e.canceled.at,
      kind: "canceled",
      textSent: false,
      textReplied: false,
      optedOut: false,
      overridden: false,
      current: false,
      entry: e,
      cancel: e.canceled,
      language: e.language || undefined,
    });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}

export function kindOf(c: CallDoc): CompletedKind {
  if (c.outcome === "unable_to_reach_wrong_person" || c.outcome === "identity_verification_failed") {
    return "wrong_person";
  }
  if (c.outcome === "consent_declined") return "declined";
  // Checked before call_status: the call "succeeded" technically, but no
  // questions were answered, so it must not read as a completion.
  if (c.outcome === "reschedule_requested") return "reschedule";
  if (c.channel === "sms") return "completed_text";
  switch (c.call_status) {
    case "success":
      return "completed_phone";
    case "left_vmail":
      return "voicemail";
    case "rna":
      return "no_answer";
    case "interrupted":
      return "interrupted";
    default:
      return "unreachable";
  }
}

export type CompletedFilter =
  | "all"
  // An open follow-up past its callback window (from followUpItems)
  | "overdue"
  // What happened on the call
  | "completed_phone"
  | "completed_text"
  | "voicemail"
  | "text_sent"
  | "callback_booked"
  | "no_answer"
  | "unreachable"
  | "interrupted"
  | "reschedule"
  | "canceled"
  // What the nurse has to do about it
  | "needs_immediate"
  | "next_business_day"
  | "no_follow_up";

/** Filters overlap on purpose: a voicemail that was followed by a text is both. */
export function matchesCompleted(item: CompletedItem, f: CompletedFilter): boolean {
  switch (f) {
    case "all":
      return true;
    case "text_sent":
      return item.textSent;
    case "callback_booked":
      return !!item.callback;
    case "unreachable":
      return item.kind === "unreachable" || item.kind === "wrong_person" || item.kind === "declined";
    case "needs_immediate":
      return item.current && (item.followUp === "emergency" || item.followUp === "urgent_same_day");
    case "next_business_day":
      return item.current && item.followUp === "routine_next_business_day";
    case "no_follow_up":
      // Only a questionnaire the agent actually assessed counts as "no
      // follow-up needed". An unanswered call has no verdict at all.
      return item.current && (item.kind === "completed_phone" || item.kind === "completed_text") &&
        (item.followUp === "none" || item.followUp === "informational");
    default:
      return item.kind === f;
  }
}

/** How far back the call-status board looks. */
export type DayScope = "today" | "week" | "all";

export function inScope(item: CompletedItem, scope: DayScope, now: Date = new Date()): boolean {
  if (scope === "all") return true;
  const t = new Date(item.at).getTime();
  if (isNaN(t)) return false;
  const start = startOfDay(now);
  if (scope === "today") return t >= start && t < start + 86_400_000;
  return t >= start - 6 * 86_400_000 && t < start + 86_400_000;
}

// ---------------------------------------------------------------------------
// Follow-ups
// ---------------------------------------------------------------------------

export type FollowUpTier = "emergency" | "urgent_same_day" | "routine_next_business_day";

export interface FollowUpItem {
  id: string;
  patientName: string;
  patientId?: string;
  phone: string;
  procedure?: string;
  tier: FollowUpTier;
  reason?: string;
  /** When the agent raised it. */
  raisedAt: string;
  /** End of the window the tier allows (5 pm on the due day; emergency: at once). */
  dueBy: string;
  overdue: boolean;
  /** A callback the patient booked by text, if one is pending. */
  callback?: ScheduledCallback;
  call: CallDoc;
  /** The patient's preferred language (absent = English (US)). */
  language?: string;
}

export const DUE_HOUR = 17;

export function followUpItems(
  calls: CallDoc[],
  callbacks: ScheduledCallback[],
  now: Date = new Date(),
): FollowUpItem[] {
  const pending = callbacks.filter((cb) => cb.status === "scheduled" || cb.status === "dialing");
  return followUpCalls(calls)
    .map((c) => {
      const raisedAt = c.ended_at ?? c.updated_at ?? c.initiated_at;
      const tier = c.follow_up as FollowUpTier;
      const dueBy = dueFor(tier, new Date(raisedAt)).toISOString();
      return {
        id: c.doc_id,
        patientName: c.patient_name ?? "Unknown patient",
        patientId: c.patient_id,
        phone: c.to,
        procedure: procedureOf(c),
        tier,
        reason: c.follow_up_reason,
        raisedAt,
        dueBy,
        // Emergency is due the moment it is raised, so "overdue" would be
        // true from birth and say nothing; it has its own alarm instead.
        overdue: tier !== "emergency" && now.getTime() > new Date(dueBy).getTime(),
        callback: pending.find((cb) => cb.phone === c.to),
        call: c,
        language: callLanguage(c),
      };
    })
    .sort(compareFollowUps);
}

/**
 * Emergency is due the moment it was raised. Same-day is due by 5 pm that
 * day (or at once, if raised after 5). Next-business-day skips weekends.
 */
export function dueFor(tier: FollowUpTier, raised: Date): Date {
  if (tier === "emergency") return raised;
  const due = new Date(raised);
  if (tier === "routine_next_business_day") {
    do due.setDate(due.getDate() + 1);
    while (due.getDay() === 0 || due.getDay() === 6);
  }
  due.setHours(DUE_HOUR, 0, 0, 0);
  return due.getTime() < raised.getTime() ? raised : due;
}

const TIER_RANK: Record<FollowUpTier, number> = {
  emergency: 0,
  urgent_same_day: 1,
  routine_next_business_day: 2,
};

function compareFollowUps(a: FollowUpItem, b: FollowUpItem): number {
  if (TIER_RANK[a.tier] !== TIER_RANK[b.tier]) return TIER_RANK[a.tier] - TIER_RANK[b.tier];
  return a.dueBy.localeCompare(b.dueBy);
}

export type FollowUpFilter = "all" | FollowUpTier | "overdue" | "due_today";

export function matchesFollowUp(item: FollowUpItem, f: FollowUpFilter, now: Date = new Date()): boolean {
  switch (f) {
    case "all":
      return true;
    case "overdue":
      return item.overdue;
    case "due_today":
      return startOfDay(new Date(item.dueBy)) <= startOfDay(now);
    default:
      return item.tier === f;
  }
}

// ---------------------------------------------------------------------------

export function procedureOf(c: CallDoc): string | undefined {
  const v = c.patient_context?.operation_name;
  return typeof v === "string" && v ? v : undefined;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return ((parts[0][0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
