export interface CallDoc {
  doc_id: string;
  call_sid?: string;
  to: string;
  from: string;
  initiated_at: string;
  updated_at: string;
  ringing_at?: string;
  answered_at?: string;
  ended_at?: string;
  duration_seconds?: number;
  call_status?: string;
  last_twilio_status?: string;
  patient_id?: string;
  patient_name?: string;
  patient_context?: Record<string, unknown>;
  session_uuid?: string;
  stream_sid?: string;
  outcome?: string;
  /** How the answers arrived. Absent or "voice" for a normal call. */
  channel?: "voice" | "sms";
  follow_up?: string;
  follow_up_reason?: string;
  dob_verification_result?: string;
  recording_uri?: string;
  recording_url?: string;
  report_doc_id?: string;
  failure_code?: string;
  failure_message?: string;
  failure_reason?: string;
  survey_responses?: Record<string, unknown>;
  placed_via?: string;
  /** The agent's own tier, kept when a nurse overrides `follow_up`. */
  agent_follow_up?: string | null;
  /** The latest nurse change to the tier, if any. */
  follow_up_override?: FollowUpEvent;
  /** Every nurse change to the tier, oldest first. */
  follow_up_history?: FollowUpEvent[];
  /** Nurse sign-off. Signing off a follow-up resolves it. */
  review?: CallReview | null;
}

export interface CallReview {
  /** superseded: closed when a LATER report for the same patient was signed
   *  off; nothing from this one went to the EHR. */
  status: "signed_off" | "reopened" | "superseded";
  superseded_by?: string;
  by?: string;
  at?: string;
  note?: string | null;
  follow_up?: string;
  /** True when the sign-off resolved a follow-up (it then leaves the follow-up lists). */
  resolved?: boolean;
  reopened_at?: string;
  reopened_because?: string;
}

/** One nurse change to a call's follow-up tier. */
export interface FollowUpEvent {
  at: string;
  from?: string | null;
  to: string;
  reason: string;
  set_by?: string;
}

export interface FollowUpOverrideRequest {
  follow_up: string;
  reason: string;
  set_by?: string;
}

export interface TranscriptTurn {
  role: "agent" | "patient";
  text: string;
  timestamp: string;
}

export interface PlaceCallRequest {
  phone: string;
  patient_name: string;
  patient_id: string;
  patient_dob: string;
  operation_name?: string;
  operation_date?: string;
  is_diabetic: boolean;
}

export interface PlaceCallResponse {
  call_sid: string;
  status: string;
}

export interface CallsListResponse {
  calls: CallDoc[];
  count: number;
}

export interface TranscriptResponse {
  turns: TranscriptTurn[];
  available: boolean;
  reason?: string;
  conversation_id?: string;
}

export interface BatchDeleteResult {
  call_sid: string;
  status: "deleted" | "partial" | "not_found" | "invalid_sid" | "error";
  removed?: {
    calls_doc: boolean;
    call_records_doc: boolean;
    post_op_reports_doc: boolean;
    gcs_objects: number;
  };
  error?: string;
}

export interface BatchDeleteResponse {
  results: BatchDeleteResult[];
}

/** One patient on the manually-run call list. */
export interface CallListEntry {
  entry_id: string;
  phone: string;
  patient_name: string;
  patient_id: string;
  patient_dob: string;
  operation_name?: string;
  operation_date?: string;
  is_diabetic: boolean;
  /**
   * pending   = will be dialed on the next run
   * calling   = in flight right now
   * completed = cleared by a successful call
   * blocked   = dispatch failed permanently (not on the outbound allowlist,
   *             or an unusable number). Skipped by future runs until the
   *             entry is edited or the list is reset.
   */
  list_status: "pending" | "calling" | "completed" | "blocked" | "canceled";
  attempts: number;
  last_attempt_at?: string;
  last_call_sid?: string;
  last_call_status?: string;
  last_error?: string;
  created_at: string;
  updated_at: string;
  /** Automatic recalls in a row since a nurse last scheduled this patient. */
  auto_recalls?: number;
  /** When the next automatic recall is planned, if one is. */
  next_recall_at?: string;
  /** Why the last missed call was not recalled, if it was not. */
  recall_skipped?: string | null;
  /** Set while list_status is "canceled": why, who and when. */
  canceled?: CancelEvent | null;
  /** Every cancellation, oldest first (kept after a reschedule). */
  cancel_history?: CancelEvent[];
  /**
   * Planned call time (ISO, UTC). Advisory for a run a nurse starts by
   * hand; binding for the scheduler's due-only runs. Null = next run.
   */
  scheduled_for?: string | null;
}

export type CallListEntryInput = PlaceCallRequest & { scheduled_for?: string | null };

/**
 * A scheduled call run: a time plus a set of call-list patients. Created
 * under Settings; the per-minute Cloud Scheduler job dials the run's
 * pending patients when its time arrives.
 */
export interface CallRun {
  run_id: string;
  scheduled_for: string;
  entry_ids: string[];
  label?: string | null;
  /** scheduled → dispatching (claimed by a tick) → dispatched. */
  status: "scheduled" | "dispatching" | "dispatched";
  created_at: string;
  updated_at: string;
  dispatched_at?: string;
  claimed_at?: string | null;
  /** Set when the run's dialling ended (finished, stopped, or carried over). */
  finished_at?: string | null;
  /** How many patients the run actually dialled (its pending ones). */
  dialled?: number;
  /** Created by auto-recall rather than by a nurse. */
  auto_recall?: boolean;
  /** Recall members skipped at run time because they answered the text. */
  skipped_replied?: string[];
  note?: string;
}

/** A nurse cancelled a patient's queued call. */
export interface CancelEvent {
  at: string;
  reason: string;
  by?: string;
  /** The run the patient was taken out of, if any. */
  run_id?: string | null;
  run_scheduled_for?: string | null;
}

/** Where to put a cancelled patient back: an existing run, or a new one. */
export type RescheduleTarget = { run_id: string } | { scheduled_for: string; label?: string | null };

/** Automatic recall of patients a run did not reach (Settings). */
export interface RecallSettings {
  enabled: boolean;
  interval_minutes: number;
  /** Clinic-local "HH:MM", window is [start, end). */
  window_start: string;
  window_end: string;
  /** Allowed weekdays, Monday = 0. */
  days: number[];
  max_recalls: number;
}

export interface CallSettingsResponse {
  recall: RecallSettings;
  timezone: string;
}

export interface CallRunInput {
  scheduled_for: string;
  entry_ids: string[];
  label?: string | null;
}

export interface CallListResponse {
  entries: CallListEntry[];
  count: number;
}

export interface RunInFlight {
  entry_id: string;
  patient_name: string;
  call_sid: string;
}

export interface CallListRun {
  run_id: string;
  status: "running" | "completed" | "canceled" | "error" | "budget_exhausted";
  started_at: string;
  finished_at: string | null;
  total: number;
  dispatched: number;
  succeeded: number;
  failed: number;
  cancel_requested: boolean;
  errors: { entry_id: string; error: string }[];
  in_flight: RunInFlight[];
}

export interface CallListRunResponse {
  active: boolean;
  run: CallListRun | null;
}

/** One entry on the outbound allowlist. */
export interface AllowlistEntry {
  phone: string;
  label: string;
  created_at?: string;
  /** "env" entries come from OUTBOUND_ALLOWLIST and can't be edited here. */
  source: "env" | "firestore";
  editable: boolean;
}

export interface AllowlistResponse {
  entries: AllowlistEntry[];
  count: number;
  mode: "unset" | "open" | "list";
  /** True when OUTBOUND_ALLOWLIST="*" — every destination is permitted. */
  unrestricted: boolean;
}
