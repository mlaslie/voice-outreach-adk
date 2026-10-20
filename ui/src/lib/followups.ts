/**
 * SMS follow-up data: types, client, and the interpretation a nurse needs.
 *
 * The interpretation lives here rather than in JSX for the same reason
 * `triage.ts` does — it is the part that can be wrong in a way nobody
 * notices, so it is separated and tested.
 *
 * The service's own vocabulary leaks through the API (`awaiting_choice`,
 * `recall_dispatched`). None of it should reach the screen: a nurse cares
 * whether this patient still needs something, not what the state machine
 * calls it.
 */

export type SessionState =
  | "awaiting_choice"
  | "awaiting_time"
  | "scheduled"
  | "recall_dispatched"
  | "dialing"
  // F9 — the questionnaire by text.
  | "awaiting_dob"
  | "survey"
  | "closed"
  | "expired"
  | "opted_out";

export type CallbackStatus =
  | "scheduled"
  | "dialing"
  | "dispatched"
  | "canceled"
  | "superseded"
  | "failed";

export interface FollowUpSession {
  phone: string;
  state?: SessionState;
  patient_name?: string;
  patient_id?: string;
  call_sid?: string;
  callback_id?: string | null;
  scheduled_for?: string | null;
  scheduled_for_text?: string | null;
  message_count?: number;
  last_invite_at?: string | null;
  last_inbound_at?: string | null;
  updated_at?: string;
  created_at?: string;
  last_error?: string | null;
}

export interface ScheduledCallback {
  callback_id: string;
  phone: string;
  run_at: string;
  status?: CallbackStatus;
  patient_name?: string;
  patient_id?: string;
  origin_call_sid?: string | null;
  call_sid?: string | null;
  last_error?: string | null;
}

export interface SmsMessage {
  id: string;
  phone: string;
  direction: "inbound" | "outbound";
  body: string;
  at: string;
  message_sid?: string | null;
  call_sid?: string | null;
}

export type Tone = "wait" | "good" | "alert" | "muted";

export interface Label {
  text: string;
  tone: Tone;
}

/** Is this conversation still going anywhere? */
/** Still waiting on somebody — a patient, or a call we owe them. */
export function isOpen(state?: SessionState): boolean {
  return (
    state === "awaiting_choice" ||
    state === "awaiting_time" ||
    state === "scheduled" ||
    state === "recall_dispatched" ||
    state === "dialing" ||
    state === "awaiting_dob" ||
    state === "survey"
  );
}

const SESSION_LABELS: Record<SessionState, Label> = {
  awaiting_choice: { text: "Waiting for reply", tone: "wait" },
  awaiting_time: { text: "Choosing a time", tone: "wait" },
  scheduled: { text: "Callback booked", tone: "good" },
  awaiting_dob: { text: "Verifying identity", tone: "wait" },
  survey: { text: "Answering by text", tone: "wait" },
  recall_dispatched: { text: "Call queued", tone: "good" },
  dialing: { text: "Calling now", tone: "good" },
  closed: { text: "Finished", tone: "muted" },
  expired: { text: "No reply", tone: "muted" },
  opted_out: { text: "Opted out", tone: "alert" },
};

export function sessionLabel(state?: SessionState): Label {
  if (!state) return { text: "Unknown", tone: "muted" };
  return SESSION_LABELS[state] ?? { text: state, tone: "muted" };
}

const CALLBACK_LABELS: Record<CallbackStatus, Label> = {
  scheduled: { text: "Booked", tone: "good" },
  dialing: { text: "Calling", tone: "good" },
  dispatched: { text: "Called", tone: "muted" },
  canceled: { text: "Cancelled", tone: "muted" },
  superseded: { text: "Replaced", tone: "muted" },
  failed: { text: "Failed", tone: "alert" },
};

export function callbackLabel(status?: CallbackStatus): Label {
  if (!status) return { text: "Unknown", tone: "muted" };
  return CALLBACK_LABELS[status] ?? { text: status, tone: "muted" };
}

/** Only a booking that has not yet been acted on can be cancelled. */
export function isCancellable(cb: ScheduledCallback): boolean {
  return cb.status === "scheduled";
}

/**
 * Bookings that are still going to happen, soonest first.
 *
 * A callback whose time has passed but which never moved off "scheduled"
 * is deliberately kept: it means the ticker did not run, and hiding it
 * would hide the outage.
 */
export function upcoming(callbacks: ScheduledCallback[]): ScheduledCallback[] {
  return callbacks
    .filter((c) => c.status === "scheduled" || c.status === "dialing")
    .sort((a, b) => (a.run_at ?? "").localeCompare(b.run_at ?? ""));
}

/** True when a booking's time has come and gone without being dialled. */
export function isOverdue(cb: ScheduledCallback, now: Date = new Date()): boolean {
  if (cb.status !== "scheduled" || !cb.run_at) return false;
  return new Date(cb.run_at).getTime() < now.getTime() - 5 * 60_000;
}

export interface FollowUpSummary {
  waiting: number;
  booked: number;
  optedOut: number;
  overdue: number;
}

export function summarize(
  sessions: FollowUpSession[],
  callbacks: ScheduledCallback[],
  now: Date = new Date()
): FollowUpSummary {
  return {
    waiting: sessions.filter(
      (s) =>
        s.state === "awaiting_choice" ||
        s.state === "awaiting_time" ||
        s.state === "awaiting_dob" ||
        s.state === "survey"
    ).length,
    booked: upcoming(callbacks).length,
    optedOut: sessions.filter((s) => s.state === "opted_out").length,
    overdue: callbacks.filter((c) => isOverdue(c, now)).length,
  };
}

/**
 * The conversation list on the Follow-ups tab.
 *
 * Two rules, both about not making an operator read the same thing twice:
 *
 * 1. A finished conversation — a completed questionnaire, an expired invite,
 *    an opt-out — has nothing outstanding and belongs on the call record.
 *    `showClosed` puts them back when you want the history.
 * 2. A BOOKED callback is excluded, because the Scheduled callbacks section
 *    directly above already lists it, with its time and a cancel button.
 *    Showing it in both places also mislabels it: the patient has replied,
 *    so nothing is waiting on them. Safe to exclude, because the two move
 *    together — when a callback is dispatched its status leaves `upcoming()`
 *    and the session is set to `closed` in the same handler, so there is no
 *    window where a booking is visible in neither place.
 */
export function openConversations(
  sessions: FollowUpSession[],
  showClosed = false
): FollowUpSession[] {
  if (showClosed) return sessions;
  return sessions.filter((s) => isOpen(s.state) && s.state !== "scheduled");
}

/** Live conversations first, then most recently touched. */
export function sortSessions(sessions: FollowUpSession[]): FollowUpSession[] {
  return [...sessions].sort((a, b) => {
    const openDiff = Number(isOpen(b.state)) - Number(isOpen(a.state));
    if (openDiff !== 0) return openDiff;
    return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
  });
}

/* ---- client ---------------------------------------------------------- */

export class FollowUpError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "FollowUpError";
  }
}

/**
 * The SMS endpoints live on the same backend as the calls, under /api/sms/*,
 * and are reached the same way: through this app's same-origin BFF route
 * (/api/backend/sms/...), never directly from the browser.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/backend/sms/${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      // keep the status text
    }
    throw new FollowUpError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

export function fetchSessions() {
  return request<{ sessions: FollowUpSession[] }>("sessions");
}

export function fetchCallbacks() {
  return request<{ callbacks: ScheduledCallback[] }>("callbacks");
}

export function fetchMessages(phone: string, callSid?: string | null) {
  // callSid scopes the thread to the exchange that followed ONE call. Without
  // it the backend returns every message ever sent to this handset, so a
  // patient who has been called twice reads as one run-on conversation.
  const q = new URLSearchParams({ phone });
  if (callSid) q.set("call_sid", callSid);
  return request<{ phone: string; messages: SmsMessage[] }>(`messages?${q}`);
}

export function cancelCallback(callbackId: string) {
  return request<{ status: string }>(`callbacks/${encodeURIComponent(callbackId)}`, {
    method: "DELETE",
  });
}

/** Whether this number has a live (not withdrawn) agreement to receive texts. */
export function fetchSmsConsent(phone: string) {
  return request<{ phone: string; granted: boolean }>(`consent?${new URLSearchParams({ phone })}`);
}

/**
 * Record whether this number agreed to receive texts.
 *
 * Deliberately a separate call from adding the number to the dial allowlist:
 * the two permissions are granted independently, and the send path refuses
 * any number without a consent record here.
 */
export function setSmsConsent(phone: string, granted: boolean, statementVersion: string) {
  return request<{ phone: string; granted: boolean }>("consent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      phone,
      granted,
      statement_version: statementVersion,
      granted_by: "dashboard",
    }),
  });
}

export interface FollowUpSettings {
  invite_cooldown_enabled: boolean;
}

export function fetchSettings() {
  return request<{ settings: FollowUpSettings }>("settings");
}

/**
 * Flip a runtime switch on the follow-up service.
 *
 * Currently only the invite cooldown, which exists so a demo can re-run the
 * same scenario without waiting 24 hours. It defaults on and fails safe.
 */
export function setSetting(name: keyof FollowUpSettings, value: boolean) {
  return request<{ settings: FollowUpSettings }>("settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, value }),
  });
}
