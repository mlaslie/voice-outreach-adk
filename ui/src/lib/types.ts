/** Whether a missed call's follow-up text went out, and if not why (set by the API). */
export interface SmsFollowUpStamp {
  status: "sent" | "not_sent" | "deferred";
  reason?: string;
  at?: string;
}

export interface CallDoc {
  doc_id: string;
  sms_follow_up?: SmsFollowUpStamp;
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
  /** The patient's preferred language when the call was placed (absent on older calls = en-US). */
  language?: string;
  call_in_english?: boolean;
  /** The language most of the patient's turns were in, set after the call by the translation task (docs/LANGUAGES-PLAN.md step 2). */
  language_used?: string;
  /** Every language the patient spoke, first seen first. */
  languages_spoken?: string[];
  campaign_id?: string;
  session_uuid?: string;
  stream_sid?: string;
  outcome?: string;
  /** How the answers arrived. Absent or "voice" for a normal call. */
  channel?: "voice" | "sms";
  follow_up?: string;
  /** One sentence. Since Oct 2026 triage writes "Patient reports X. Also noted: Y (noted)."; after a nurse change, "Nurse: a; b." */
  follow_up_reason?: string;
  /** Every finding with its own tier, most urgent first. Absent on older calls (fall back to follow_up_reason). */
  follow_up_reasons?: FollowUpReason[];
  dob_verification_result?: string;
  recording_uri?: string;
  recording_url?: string;
  report_doc_id?: string;
  failure_code?: string;
  failure_message?: string;
  failure_reason?: string;
  /** Known keys are typed; old calls lack the newer ones. Treat every key as optional. */
  survey_responses?: SurveyResponses;
  placed_via?: string;
  /** The agent's own tier, kept when a nurse overrides `follow_up`. */
  agent_follow_up?: string | null;
  /** The agent's own reasons, kept when a nurse replaces the reasons list. */
  agent_follow_up_reasons?: FollowUpReason[];
  /** The agent's own sentence, kept when a nurse replaces the reasons list. */
  agent_follow_up_reason?: string | null;
  /** The latest nurse change to the tier, if any. */
  follow_up_override?: FollowUpEvent;
  /** Every nurse change to the tier, oldest first. */
  follow_up_history?: FollowUpEvent[];
  /** Nurse sign-off. Signing off a follow-up resolves it. */
  review?: CallReview | null;
}

export type YesNoUnknown = "yes" | "no" | "unknown";

export type QuestionCategory =
  | "medication"
  | "symptom"
  | "appointment"
  | "activity"
  | "diet"
  | "paperwork_billing"
  | "supplies"
  | "recovery"
  | "other";

export type QuestionPriority = "urgent_same_day" | "routine_next_business_day";

/** A question the patient asked the care team, in their own words. */
export interface PatientQuestion {
  text: string;
  category: QuestionCategory;
  priority: QuestionPriority;
  /**
   * Knowledge base (docs/KNOWLEDGE-PLAN.md), only on calls made with it on:
   * "answered" when the agent read an approved clinic Q&A answer, "deferred"
   * when it left the question for a nurse. Absent on older calls.
   */
  outcome?: "answered" | "deferred";
  /** The Q&A entry that answered it, and its version at the time. */
  item_id?: string;
  item_version?: number;
  /** The answer as the patient heard it. */
  answer?: string;
  /** "Did that answer your question?": false means it did not; null, not asked. */
  satisfied?: boolean | null;
  /** Live answers (KNOWLEDGE-PLAN v2): the material the answer came from. */
  sources?: KnowledgeSource[];
  /** Live answers: the grounding check's support score (0–1). */
  support_score?: number;
  /** Shadow mode: what the agent would have said. Never said to the patient. */
  shadow?: ShadowDraft;
  /** A nurse's rating of the shadow draft. */
  shadow_review?: ShadowReview;
}

/** One piece of approved material an answer drew on. */
export interface KnowledgeSource {
  kind: "example" | "guideline";
  id: string;
  title: string;
  version?: number;
}

/** Shadow mode: the agent's draft for a question, run after the call. */
export interface ShadowDraft {
  outcome: "answered" | "deferred";
  /** One of the knowledge reasons (lib/knowledge.ts REASONS). */
  reason: string;
  answer?: string;
  sources?: KnowledgeSource[];
  support_score?: number;
  at?: string;
}

export type ShadowVerdict = "good" | "not_right";

export interface ShadowReview {
  verdict: ShadowVerdict;
  note?: string | null;
  by?: string | null;
  at?: string | null;
}

/** Inpatient or outpatient care (optional on call-list entries and calls). */
export type CareSetting = "inpatient" | "outpatient";

/**
 * Post-op questionnaire answers (voice and SMS). The 19 Oct 2026 version
 * added legs_swollen, swelling_description, sleeping_well,
 * blood_sugar_reading, has_questions and patient_questions; older calls
 * lack them. Underscore keys are service bookkeeping, not answers.
 */
export interface SurveyResponses {
  overall_feeling?: string;
  pain_present?: YesNoUnknown | string;
  pain_severity?: number | string;
  pain_severity_note?: string;
  /** Since 19 Oct 2026: the patient's description of the pain. */
  pain_location?: string;
  pain_trend?: "better" | "same" | "worse" | string;
  fever_reported?: YesNoUnknown | string;
  incision_concerns?: YesNoUnknown | string;
  incision_description?: string;
  legs_swollen?: YesNoUnknown | string;
  /** One leg or both, getting worse? */
  swelling_description?: string;
  sleeping_well?: YesNoUnknown | string;
  blood_sugar_status?: "high" | "low" | "in_range" | "unknown" | string;
  /** The patient's own words, e.g. "between 120 and 250". */
  blood_sugar_reading?: string;
  has_questions?: "yes" | "no" | string;
  patient_questions?: PatientQuestion[];
  [key: string]: unknown;
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

export type FollowUpReasonTier =
  | "emergency"
  | "urgent_same_day"
  | "routine_next_business_day"
  | "informational"
  | "none";

/** One reason a call needs (or does not need) follow-up. */
export interface FollowUpReason {
  tier: FollowUpReasonTier | string;
  text: string;
  /** triage: automatic, from the patient's answers; agent: the voice agent; nurse: a nurse's change. */
  source?: "triage" | "agent" | "nurse" | string;
  /** Answer keys the finding came from, e.g. ["sleeping_well"]. */
  fields?: string[];
  set_by?: string;
  at?: string;
}

/** One nurse change to a call's follow-up tier. */
export interface FollowUpEvent {
  at: string;
  from?: string | null;
  to: string;
  /** The note on the change (defaults to the reasons joined). */
  reason: string;
  /** The reasons the nurse chose, when the change carried a list. */
  reasons?: string[];
  set_by?: string;
}

/** At least one of `reason` (a note, >= 3 chars) or `reasons` (1-10, each <= 200 chars). */
export interface FollowUpOverrideRequest {
  follow_up: string;
  reason?: string;
  reasons?: string[];
  set_by?: string;
}

export interface TranscriptTurn {
  role: "agent" | "patient";
  /** The words as spoken, in the speaker's language. */
  text: string;
  /** English translation; null/absent when the turn is English or translation has not run. */
  text_en?: string | null;
  /** Detected language of the turn (e.g. "es-US"); absent before translation. */
  lang?: string | null;
  timestamp: string;
}

/** The post-call translation of a transcript (null/absent: not run yet). */
export interface TranscriptTranslation {
  status: "done" | "failed" | "skipped";
  model?: string | null;
  at?: string;
  languages?: string[];
  error?: string;
}

export interface PlaceCallRequest {
  phone: string;
  patient_name: string;
  patient_id: string;
  patient_dob: string;
  operation_name?: string;
  operation_date?: string;
  is_diabetic: boolean;
  /** Optional; null clears it on an edit. */
  care_setting?: CareSetting | null;
  /** Preferred language, one of GET /api/languages (default en-US). */
  language?: string;
  /** An admin's override: call in English while the language is not available. */
  call_in_english?: boolean;
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
  translation?: TranscriptTranslation | null;
  /** The language asked for when the call was placed. */
  language?: string | null;
  /** The language the patient mostly spoke (set by the translation task). */
  language_used?: string | null;
  /** The use case's display title, for the download. */
  title?: string;
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
  care_setting?: CareSetting | null;
  /** Preferred language (BCP-47, e.g. es-US). Absent on older entries = en-US. */
  language?: string | null;
  /** Call in English although the language is not available (admin override). */
  call_in_english?: boolean;
  /** The use case; absent = the default (post-op). */
  campaign_id?: string | null;
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

/** How nurse-facing times render (patients always get 12-hour). */
export interface DisplaySettings {
  time_format: "12h" | "24h";
}

export interface CallSettingsResponse {
  recall: RecallSettings;
  timezone: string;
  /** Optional until every backend serves it; treat missing as 24h. */
  display?: DisplaySettings;
  /** The company name patients hear and read (voice script, voicemail, texts). */
  organization?: OrganizationSettings;
}

export interface OrganizationSettings {
  org_name: string;
  /** The configured name (site.org_name) that "Reset" returns to. */
  default: string;
  is_default: boolean;
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

/**
 * One gateway event on a call's millisecond timeline (Admin → Call
 * diagnostics). `ms` counts from the gateway session's start. Events carry
 * names and statuses only (tool names and results), never patient answers.
 */
export interface CallEvent {
  ms: number;
  event: string;
  [detail: string]: unknown;
}

export interface CallEventsSummary {
  events: number;
  /** media.start to the agent's first audio, or null when either is missing. */
  first_audio_ms: number | null;
  tool_calls: number;
  interruptions: number;
  /** live.* events. */
  errors: number;
}

/** GET /api/calls/{id}/events (admin only). */
export interface CallEventsResponse {
  call_id: string;
  /** False when the call has no timeline (an SMS-only call, a call that never connected). */
  available: boolean;
  timeline: CallEvent[];
  summary: CallEventsSummary;
}

/** One configuration change (GET /api/audit, admin only). */
export interface AuditEntry {
  id: string;
  at: string;
  /** The signed-in user (X-Acting-User), or null for a service caller. */
  by: string | null;
  /** The route template, e.g. "PUT /api/call-settings/display". */
  action: string;
  /** Path parameters, phone numbers masked: {phone: "+1•••4567"}. */
  target: Record<string, string>;
}

export interface AuditResponse {
  entries: AuditEntry[];
}
