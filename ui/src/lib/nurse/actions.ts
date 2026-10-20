import {
  BatchDeleteResult, CallDoc, CallListEntry, CallListEntryInput, CallRun, CallRunInput, DisplaySettings, FollowUpOverrideRequest, RecallSettings, RescheduleTarget,
} from "../types";

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
  /** A nurse changes the follow-up tier, with a reason. Returns the updated call. */
  setFollowUp(callSid: string, body: FollowUpOverrideRequest): Promise<CallDoc>;
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
  /** Authorise calls to a number, and record its answer on texts (optional, separate). */
  addNumber(phone: string, label: string, smsConsent: boolean): Promise<void>;
  /** Stop authorising calls to a number. */
  removeNumber(phone: string): Promise<void>;
  /** Record or withdraw a number's agreement to receive texts. */
  setConsent(phone: string, granted: boolean): Promise<void>;
  /** Re-read everything (after an action). */
  refresh(): void;
}

export const FOLLOW_UP_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "emergency", label: "Emergency", hint: "Call the patient now; 911 guidance may apply." },
  { value: "urgent_same_day", label: "Urgent — same day", hint: "A nurse calls back today." },
  { value: "routine_next_business_day", label: "Next business day", hint: "A nurse calls back on the next working day." },
  { value: "informational", label: "Informational", hint: "Noted for the chart; no callback." },
  { value: "none", label: "No follow-up needed", hint: "Assessed and cleared." },
];
