import {
  CallDoc, CallListEntry, CallListEntryInput, CallRun, CallRunInput, FollowUpOverrideRequest, RecallSettings, RescheduleTarget,
} from "../types";

/**
 * What the nurse console can DO, as opposed to what it shows.
 *
 * The page wires these to the real API (live) or to no-ops that explain
 * themselves (fictional sample data). Keeping them behind one interface
 * means the screens never know which, and never import the API client.
 */
export interface NurseActions {
  /** False on fictional sample data: buttons explain instead of acting. */
  live: boolean;
  /** Start a run over exactly these call-list entries, now, regardless of their planned time. */
  callEntries(entryIds: string[]): Promise<void>;
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
  /** Cancel queued calls with a reason; they move to Call status as Cancelled. */
  cancelEntries(entryIds: string[], reason: string): Promise<void>;
  /** Put a cancelled patient back on the queue. */
  rescheduleEntry(entryId: string, target: RescheduleTarget): Promise<void>;
  /** Save the auto-recall settings. */
  saveRecall(body: RecallSettings): Promise<void>;
  /** A nurse changes the follow-up tier, with a reason. Returns the updated call. */
  setFollowUp(callSid: string, body: FollowUpOverrideRequest): Promise<CallDoc>;
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
