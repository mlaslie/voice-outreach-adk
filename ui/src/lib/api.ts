import {
  AllowlistEntry,
  AllowlistResponse,
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
  PlaceCallResponse,
  TranscriptResponse,
} from "./types";

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

  placeCall(body: PlaceCallRequest): Promise<PlaceCallResponse> {
    return request<PlaceCallResponse>("/place-call", json("POST", body));
  },

  /**
   * Start a browser test call: the backend stages a patient (a call-list entry,
   * or the fictional test patient) and returns a short-lived softphone URL on the
   * gateway. No phone line is used.
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

  /** Same-origin SSE feed of call updates (proxied, streamed, by the BFF). */
  streamUrl(): string {
    return `${BACKEND_PREFIX}/calls/stream`;
  },

  recordingUrl(sid: string): string {
    return `${BACKEND_PREFIX}/calls/${encodeURIComponent(sid)}/recording`;
  },
};
