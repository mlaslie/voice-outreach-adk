import {
  AllowlistEntry,
  AllowlistResponse,
  AuditResponse,
  CallEventsResponse,
  BatchDeleteResponse,
  CallDoc,
  CallListEntry,
  CallListEntryInput,
  CallListResponse,
  CallListRunResponse,
  CallRun,
  CallRunInput,
  CallSettingsResponse,
  DisplaySettings,
  RecallSettings,
  RescheduleTarget,
  CallsListResponse,
  FollowUpOverrideRequest,
  PlaceCallRequest,
  ShadowVerdict,
  PlaceCallResponse,
  TranscriptResponse,
} from "./types";
import type { SmsMessage } from "./followups";
import type { LanguagesResponse } from "./languages";
import type { AssetKind, BrandingPatch, BrandingResponse } from "./branding";
import type {
  KnowledgeDoc,
  KnowledgeDocInput,
  KnowledgeItem,
  KnowledgeItemInput,
  KnowledgeSettingsPatch,
  KnowledgeTryContext,
  KnowledgeTryResult,
} from "./knowledge";

/** Dry-run SMS simulator (POST /api/sms/test/*). Never sends a real text. */
export interface SmsTestPatient {
  patient_name: string;
  patient_id: string;
  patient_dob: string;
  is_diabetic: boolean;
}

export interface SmsTestStartResponse {
  phone: string;
  call_sid: string;
  dry_run: true;
  survey_enabled: boolean;
  /** "simulator": a test-only consent was recorded; "existing": one was on file. */
  consent: "simulator" | "existing";
  patient: SmsTestPatient;
  state: string;
  messages: SmsMessage[];
}

export interface SmsTestReplyResponse {
  phone: string;
  call_sid: string;
  result: { note?: string; effect?: string; replies?: number };
  state: string | null;
  report_id: string | null;
  new_messages: SmsMessage[];
  messages: SmsMessage[];
}

/**
 * Every backend call goes through this app's own same-origin BFF route,
 * /api/backend/<path> (src/app/api/backend/[...path]/route.ts), which adds
 * the Google ID token and the signed-in user on the server. The browser
 * never learns the backend's URL. Allowed paths: ALLOWED_ROUTES in ./bff.ts.
 */
export const BACKEND_PREFIX = "/api/backend";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const JSON_HEADERS = { "Content-Type": "application/json" } as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_PREFIX}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
      if (Array.isArray(detail) && detail[0]?.msg) {
        detail = detail[0].msg;
      }
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: JSON_HEADERS, body: JSON.stringify(body) };
}

export const api = {
  getCalls(params?: { limit?: number; status?: string }): Promise<CallsListResponse> {
    const qs = new URLSearchParams();
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.status) qs.set("status", params.status);
    const q = qs.toString();
    return request<CallsListResponse>(`/calls${q ? `?${q}` : ""}`);
  },

  /** Every open follow-up from the whole collection (the call stream holds only the newest 100). */
  getOpenFollowUps(): Promise<CallsListResponse> {
    return request<CallsListResponse>("/follow-ups/open");
  },

  getCall(sid: string): Promise<CallDoc> {
    return request<CallDoc>(`/calls/${encodeURIComponent(sid)}`);
  },

  getTranscript(sid: string): Promise<TranscriptResponse> {
    return request<TranscriptResponse>(`/calls/${encodeURIComponent(sid)}/transcript`);
  },

  /** Admin only: the gateway's millisecond event timeline for one call (names and statuses, no answers). */
  getCallEvents(sid: string): Promise<CallEventsResponse> {
    return request<CallEventsResponse>(`/calls/${encodeURIComponent(sid)}/events`);
  },

  /** Admin only: configuration changes, newest first. */
  getAudit(limit = 200): Promise<AuditResponse> {
    return request<AuditResponse>(`/audit?limit=${encodeURIComponent(String(limit))}`);
  },

  placeCall(body: PlaceCallRequest): Promise<PlaceCallResponse> {
    return request<PlaceCallResponse>("/place-call", json("POST", body));
  },

  /**
   * Admin only. Start a browser test call: the backend stages a patient (a
   * call-list entry, or the fictional test patient) and returns a short-lived
   * softphone URL on the gateway. No phone line is used.
   */
  startTestCall(entryId?: string): Promise<{ call_sid: string; softphone_url: string; expires_in: number }> {
    return request("/test-call", json("POST", entryId ? { entry_id: entryId } : {}));
  },

  /** Irreversible: deletes the call records and their recordings. */
  batchDelete(callSids: string[]): Promise<BatchDeleteResponse> {
    return request<BatchDeleteResponse>("/calls/batch-delete", json("POST", { call_sids: callSids }));
  },

  /**
   * A nurse changes the follow-up tier with a reason. Who made the change is
   * taken by the backend from the signed-in user (X-Acting-User), so
   * `set_by` is only a fallback for local development.
   */
  setFollowUp(sid: string, body: FollowUpOverrideRequest): Promise<CallDoc> {
    return request<CallDoc>(`/calls/${encodeURIComponent(sid)}/follow-up`, json("POST", body));
  },

  /** The 24 patient languages and which can be called today, per use case. */
  getLanguages(): Promise<LanguagesResponse> {
    return request<LanguagesResponse>("/languages");
  },

  // ---- Call list ----------------------------------------------------------

  getCallList(): Promise<CallListResponse> {
    return request<CallListResponse>("/call-list");
  },

  createCallListEntry(body: CallListEntryInput): Promise<CallListEntry> {
    return request<CallListEntry>("/call-list", json("POST", body));
  },

  bulkCreateCallListEntries(entries: CallListEntryInput[]): Promise<CallListResponse> {
    return request<CallListResponse>("/call-list/bulk", json("POST", entries));
  },

  updateCallListEntry(entryId: string, body: CallListEntryInput): Promise<CallListEntry> {
    return request<CallListEntry>(`/call-list/${encodeURIComponent(entryId)}`, json("PATCH", body));
  },

  deleteCallListEntry(entryId: string): Promise<{ deleted: string }> {
    return request<{ deleted: string }>(`/call-list/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    });
  },

  resetCallList(): Promise<{ reset: number }> {
    return request<{ reset: number }>("/call-list/reset", { method: "POST" });
  },

  getCallListRun(): Promise<CallListRunResponse> {
    return request<CallListRunResponse>("/call-list/run");
  },

  startCallListRun(entryIds?: string[]): Promise<CallListRunResponse> {
    return request<CallListRunResponse>("/call-list/run", json("POST", { entry_ids: entryIds ?? null }));
  },

  cancelCallListRun(): Promise<CallListRunResponse> {
    return request<CallListRunResponse>("/call-list/run/cancel", { method: "POST" });
  },

  // ---- Scheduled call runs ------------------------------------------------

  getCallRuns(): Promise<{ runs: CallRun[]; count: number }> {
    return request<{ runs: CallRun[]; count: number }>("/call-runs");
  },

  createCallRun(body: CallRunInput): Promise<CallRun> {
    return request<CallRun>("/call-runs", json("POST", body));
  },

  /** Removing every patient deletes the run: the response is then { deleted }. */
  updateCallRun(runId: string, body: Partial<CallRunInput>): Promise<CallRun | { deleted: string }> {
    return request<CallRun | { deleted: string }>(
      `/call-runs/${encodeURIComponent(runId)}`,
      json("PATCH", body)
    );
  },

  cancelCallRun(runId: string): Promise<{ deleted: string }> {
    return request<{ deleted: string }>(`/call-runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    });
  },

  /** Cancel queued calls with a reason. Who cancelled comes from the signed-in user. */
  cancelEntries(entryIds: string[], reason: string): Promise<{ results: { entry_id: string; status: string }[] }> {
    return request("/call-list/cancel", json("POST", { entry_ids: entryIds, reason }));
  },

  rescheduleEntry(entryId: string, target: RescheduleTarget): Promise<{ run_id: string; scheduled_for: string }> {
    return request(`/call-list/${encodeURIComponent(entryId)}/reschedule`, json("POST", target));
  },

  getCallSettings(): Promise<CallSettingsResponse> {
    return request<CallSettingsResponse>("/call-settings");
  },

  /** Decides when patients are dialled. */
  setRecallSettings(body: RecallSettings): Promise<CallSettingsResponse> {
    return request<CallSettingsResponse>("/call-settings/recall", json("PUT", body));
  },

  /** How times show for nurses (console + Gemini Enterprise agent). */
  setDisplaySettings(body: DisplaySettings): Promise<CallSettingsResponse> {
    return request<CallSettingsResponse>("/call-settings/display", json("PUT", body));
  },

  /** The company name in the voice script, voicemail and texts; "" resets it. */
  setOrganization(orgName: string): Promise<CallSettingsResponse> {
    return request<CallSettingsResponse>("/call-settings/organization", json("PUT", { org_name: orgName }));
  },

  // ---- Outbound allowlist -------------------------------------------------
  //
  // Authorising a number means authorising real calls to it; the backend
  // checks the signed-in user's role before accepting a write.

  getAllowlist(): Promise<AllowlistResponse> {
    return request<AllowlistResponse>("/allowlist");
  },

  async addAllowlistEntry(phone: string, label: string): Promise<AllowlistEntry> {
    return request<AllowlistEntry>("/allowlist", json("POST", { phone, label }));
  },

  async removeAllowlistEntry(phone: string): Promise<{ deleted: string }> {
    return request<{ deleted: string }>(`/allowlist/${encodeURIComponent(phone)}`, {
      method: "DELETE",
    });
  },

  // ---- Dry-run SMS simulator ---------------------------------------------
  //
  // The backend refuses both (409) unless SMS is in dry-run mode, and only for
  // numbers on the outbound allowlist. Read the thread with fetchMessages
  // (lib/followups.ts) scoped to the returned call_sid.

  startSmsTest(phone: string, diabetic: boolean): Promise<SmsTestStartResponse> {
    return request<SmsTestStartResponse>("/sms/test/start", json("POST", { phone, diabetic }));
  },

  replySmsTest(phone: string, body: string): Promise<SmsTestReplyResponse> {
    return request<SmsTestReplyResponse>("/sms/test/reply", json("POST", { phone, body }));
  },

  // ---- Staff-console branding (Admin → Branding) -------------------------

  getBranding(): Promise<BrandingResponse> {
    return request<BrandingResponse>("/branding");
  },

  /** Partial update; "" or null puts a field back to its default. */
  saveBranding(patch: BrandingPatch): Promise<BrandingResponse> {
    return request<BrandingResponse>("/branding", json("PUT", patch));
  },

  /** Reset to defaults: deletes every setting and uploaded image. */
  resetBranding(): Promise<BrandingResponse> {
    return request<BrandingResponse>("/branding", { method: "DELETE" });
  },

  /** PNG, JPEG, WebP or ICO (never SVG), checked again by the API. */
  uploadBrandingAsset(kind: AssetKind, contentType: string, dataBase64: string): Promise<BrandingResponse> {
    return request<BrandingResponse>(
      `/branding/assets/${kind}`,
      json("PUT", { content_type: contentType, data_base64: dataBase64 }),
    );
  },

  // ---- Knowledge base (Admin → Knowledge base; docs/KNOWLEDGE-PLAN.md) ----
  //
  // Admin build only. Every write is audited by the API; a 422 carries the
  // reason (length, or a PHI-looking pattern) in `detail`.

  getKnowledgeItems(): Promise<{ items: KnowledgeItem[] }> {
    return request<{ items: KnowledgeItem[] }>("/admin/knowledge/items");
  },

  createKnowledgeItem(body: KnowledgeItemInput): Promise<KnowledgeItem> {
    return request<KnowledgeItem>("/admin/knowledge/items", json("POST", body));
  },

  /** Editing an approved entry sends it back to draft with version + 1. */
  updateKnowledgeItem(itemId: string, body: Partial<KnowledgeItemInput>): Promise<KnowledgeItem> {
    return request<KnowledgeItem>(`/admin/knowledge/items/${encodeURIComponent(itemId)}`, json("PATCH", body));
  },

  approveKnowledgeItem(itemId: string): Promise<KnowledgeItem> {
    return request<KnowledgeItem>(`/admin/knowledge/items/${encodeURIComponent(itemId)}/approve`, { method: "POST" });
  },

  retireKnowledgeItem(itemId: string): Promise<KnowledgeItem> {
    return request<KnowledgeItem>(`/admin/knowledge/items/${encodeURIComponent(itemId)}/retire`, { method: "POST" });
  },

  /** Drafts and retired entries only; an approved one is refused (409: "Retire it first."). */
  deleteKnowledgeItem(itemId: string): Promise<{ deleted: true }> {
    return request<{ deleted: true }>(`/admin/knowledge/items/${encodeURIComponent(itemId)}`, { method: "DELETE" });
  },

  /**
   * Raw settings: lib/knowledge normaliseSettings() turns them into the
   * panel's shape (and reads a step-1 `enabled` document as off / live).
   */
  getKnowledgeSettings(): Promise<unknown> {
    return request<unknown>("/admin/knowledge/settings");
  },

  /** Partial update; returns the whole (raw) settings document. */
  saveKnowledgeSettings(patch: KnowledgeSettingsPatch): Promise<unknown> {
    return request<unknown>("/admin/knowledge/settings", json("PUT", patch));
  },

  /** What the agent would do with a question, for an optional patient context. Not audited, nothing recorded. */
  tryKnowledge(question: string, context?: KnowledgeTryContext): Promise<KnowledgeTryResult> {
    const body = context && Object.keys(context).length ? { question, context } : { question };
    return request<KnowledgeTryResult>("/admin/knowledge/try", json("POST", body));
  },

  // Guidelines: the same lifecycle as items (draft → approved → retired).

  getKnowledgeDocs(): Promise<{ documents: KnowledgeDoc[] }> {
    return request<{ documents: KnowledgeDoc[] }>("/admin/knowledge/documents");
  },

  createKnowledgeDoc(body: KnowledgeDocInput): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>("/admin/knowledge/documents", json("POST", body));
  },

  /** Editing an approved guideline sends it back to draft with version + 1. */
  updateKnowledgeDoc(docId: string, body: Partial<KnowledgeDocInput>): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(`/admin/knowledge/documents/${encodeURIComponent(docId)}`, json("PATCH", body));
  },

  /** 409 when approving would pass the 200,000-character budget. */
  approveKnowledgeDoc(docId: string): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(`/admin/knowledge/documents/${encodeURIComponent(docId)}/approve`, { method: "POST" });
  },

  retireKnowledgeDoc(docId: string): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(`/admin/knowledge/documents/${encodeURIComponent(docId)}/retire`, { method: "POST" });
  },

  /** Drafts and retired guidelines only; an approved one is refused (409). */
  deleteKnowledgeDoc(docId: string): Promise<unknown> {
    return request<unknown>(`/admin/knowledge/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
  },

  /**
   * Nurse build: rate the agent's shadow draft for one question. `index` is
   * the question's position in the call's survey_responses.patient_questions.
   * The response shape is not fixed by the plan; lib/patient-questions
   * reviewFromResponse() reads it.
   */
  reviewShadowDraft(callId: string, index: number, body: { verdict: ShadowVerdict; note?: string }): Promise<unknown> {
    return request<unknown>(
      `/calls/${encodeURIComponent(callId)}/questions/${encodeURIComponent(String(index))}/shadow-review`,
      json("POST", body),
    );
  },

  /** Same-origin SSE feed of call updates (proxied, streamed, by the BFF). */
  streamUrl(): string {
    return `${BACKEND_PREFIX}/calls/stream`;
  },

  recordingUrl(sid: string): string {
    return `${BACKEND_PREFIX}/calls/${encodeURIComponent(sid)}/recording`;
  },
};
