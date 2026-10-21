import {
  AuditEntry, BatchDeleteResult, CallDoc, CallEventsResponse, CallListEntry, CallListEntryInput, CallRun, CallRunInput, DisplaySettings,
  FollowUpOverrideRequest, PlaceCallRequest, RecallSettings, RescheduleTarget, ShadowReview, ShadowVerdict,
} from "../types";
import type { SmsMessage } from "../followups";
import type { ImportResult } from "../call-list-csv";

/**
 * What each console can DO, as opposed to what it shows.
 *
 * The pages wire these to the real API (live) or to no-ops that explain
 * themselves (fictional sample data). Keeping them behind one interface
 * means the screens never know which, and never import the API client.
 *
 * The two sets follow the BFF's route scopes (lib/bff.ts): NurseActions only
 * reach "nurse" routes, AdminActions only "admin" routes, so each build's
 * screens cannot even be wired to the other build's writes.
 */
export interface NurseActions {
  /** False on fictional sample data: buttons explain instead of acting. */
  live: boolean;
  /** Start a run over exactly these call-list entries, now, regardless of their planned time. */
  callEntries(entryIds: string[]): Promise<void>;
  /** Cancel queued calls with a reason; they move to Call status as Cancelled. */
  cancelEntries(entryIds: string[], reason: string): Promise<void>;
  /** Put a cancelled patient back on the queue. */
  rescheduleEntry(entryId: string, target: RescheduleTarget): Promise<void>;
  /**
   * Call one patient now with details typed in (not from the call list). The
   * number must be authorized. Returns the new call's id, or null on sample data.
   */
  placeCall(body: PlaceCallRequest): Promise<string | null>;
  /** A nurse changes the follow-up tier, with a reason. Returns the updated call. */
  setFollowUp(callSid: string, body: FollowUpOverrideRequest): Promise<CallDoc>;
  /**
   * Rate the knowledge agent's shadow draft for one question (`index` in the
   * call's patient_questions). Returns the review as recorded.
   */
  reviewShadow(call: CallDoc, index: number, body: { verdict: ShadowVerdict; note?: string }): Promise<ShadowReview>;
  /**
   * Permanently delete calls (record, answers, transcript, recording) by
   * call doc id, in chunks of 100. Never throws for a failed chunk: those ids
   * come back as errors. `onChunk` reports each chunk as it lands.
   */
  deleteCalls(
    callIds: string[],
    onChunk?: (results: BatchDeleteResult[], done: number, total: number) => void,
  ): Promise<BatchDeleteResult[]>;
  /** Re-read everything (after an action). */
  refresh(): void;
}

/** Configuration, in the admin console only. */
export interface AdminActions {
  /** False on fictional sample data: buttons explain instead of acting. */
  live: boolean;
  /** Create (entry null) or update a call-list entry. */
  saveEntry(entry: CallListEntry | null, body: CallListEntryInput): Promise<void>;
  deleteEntry(entryId: string): Promise<void>;
  /**
   * Add checked CSV rows to the call list, at most 200 per request (the
   * backend's limit). Never throws for a refused chunk: it is counted as
   * rejected in the result.
   */
  importEntries(entries: CallListEntryInput[], onProgress?: (done: number, total: number) => void): Promise<ImportResult>;
  /** Put every completed entry back to pending. */
  resetCompleted(): Promise<void>;
  /** Schedule a run (at least one patient). */
  createRun(body: CallRunInput): Promise<void>;
  /** Change a scheduled run. An empty patient list deletes it. */
  updateRun(run: CallRun, body: Partial<CallRunInput>): Promise<void>;
  /** Cancel (delete) a scheduled run. */
  cancelRun(run: CallRun): Promise<void>;
  /** Save the auto-recall settings (also the clinic's calling hours). */
  saveRecall(body: RecallSettings): Promise<void>;
  /** Save how times show for nurses (12- or 24-hour). */
  saveDisplay(body: DisplaySettings): Promise<void>;
  /** The company name patients hear and read; "" resets it to the configured one. */
  saveOrganization(orgName: string): Promise<void>;
  /** Authorise calls to a number, and record its answer on texts (optional, separate). */
  addNumber(phone: string, label: string, smsConsent: boolean): Promise<void>;
  /** Stop authorising calls to a number. */
  removeNumber(phone: string): Promise<void>;
  /** Record or withdraw a number's agreement to receive texts. */
  setConsent(phone: string, granted: boolean): Promise<void>;
  /** Cancel a callback a patient booked by text. Nobody is called. */
  cancelCallback(callbackId: string): Promise<void>;
  /**
   * Start a browser test call as the fictional test patient (no phone line).
   * Returns the short-lived softphone link, or null on sample data.
   */
  startTestCall(): Promise<{ softphone_url: string; expires_in: number } | null>;
  /** Re-read everything (after an action). */
  refresh(): void;
}

/**
 * What the admin console reads on demand (a thread, a call's timeline, the
 * audit log). Live: the API. Sample data: fictional values, no request.
 */
export interface AdminReads {
  messages(phone: string, callSid?: string | null): Promise<SmsMessage[]>;
  callEvents(callId: string): Promise<CallEventsResponse>;
  audit(): Promise<AuditEntry[]>;
}

export const FOLLOW_UP_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "emergency", label: "Emergency", hint: "Call the patient now; 911 guidance may apply." },
  { value: "urgent_same_day", label: "Urgent — same day", hint: "A nurse calls back today." },
  { value: "routine_next_business_day", label: "Next business day", hint: "A nurse calls back on the next working day." },
  { value: "informational", label: "Informational", hint: "Noted for the chart; no callback." },
  { value: "none", label: "No follow-up needed", hint: "Assessed and cleared." },
];
