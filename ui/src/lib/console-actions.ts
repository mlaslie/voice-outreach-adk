import { toast } from "sonner";
import { api } from "./api";
import { CONSENT_VERSION, consentLabel } from "./consent";
import { cancelCallback, fetchMessages, setSmsConsent } from "./followups";
import { deleteInChunks } from "./nurse/delete-calls";
import { BULK_LIMIT, importInChunks } from "./call-list-csv";
import { AdminActions, AdminReads, NurseActions } from "./nurse/actions";
import { CallSettingsResponse } from "./types";
import { reviewFromResponse } from "./patient-questions";

/**
 * The live implementations of the console actions, shared by the nurse page
 * (/nurse) and the admin page (/admin). Each calls only its own build's API
 * routes (lib/bff.ts), then re-reads what the action changed.
 */

export interface NurseRefreshers {
  refresh(): void;
  refreshCalls(): void;
  refreshEntries(): void;
  refreshRun(): void;
  refreshRuns(): void;
}

export function liveNurseActions(r: NurseRefreshers): NurseActions {
  return {
    live: true,
    async callEntries(ids) {
      await api.startCallListRun(ids);
      r.refreshRun();
      r.refreshEntries();
    },
    async cancelEntries(ids, reason) {
      await api.cancelEntries(ids, reason);
      r.refreshEntries();
      r.refreshRuns(); // a cancelled patient leaves their run
    },
    async rescheduleEntry(entryId, target) {
      await api.rescheduleEntry(entryId, target);
      r.refreshEntries();
      r.refreshRuns();
    },
    async placeCall(body) {
      const res = await api.placeCall(body);
      r.refreshCalls();
      return res.call_sid;
    },
    async setFollowUp(sid, body) {
      const updated = await api.setFollowUp(sid, body);
      r.refreshCalls();
      return updated;
    },
    async reviewShadow(call, index, body) {
      const res = await api.reviewShadowDraft(call.call_sid ?? call.doc_id, index, body);
      r.refreshCalls();
      return reviewFromResponse(res, index, { verdict: body.verdict, note: body.note ?? null, by: null, at: new Date().toISOString() });
    },
    async deleteCalls(callIds, onChunk) {
      try {
        return await deleteInChunks(callIds, (part) => api.batchDelete(part), onChunk);
      } finally {
        r.refresh(); // callbacks, sessions and runs may have pointed at these calls
      }
    },
    refresh: r.refresh,
  };
}

export interface AdminRefreshers {
  refresh(): void;
  refreshFollowUps(): void;
  refreshEntries(): void;
  refreshRuns(): void;
  refreshAllowlist(): void;
  setSettings(s: CallSettingsResponse): void;
}

export function liveAdminActions(r: AdminRefreshers): AdminActions {
  return {
    live: true,
    async saveEntry(entry, body) {
      if (entry) await api.updateCallListEntry(entry.entry_id, body);
      else await api.createCallListEntry(body);
      r.refreshEntries();
    },
    async deleteEntry(id) {
      await api.deleteCallListEntry(id);
      r.refreshEntries();
      r.refreshRuns(); // the backend drops a deleted patient from their run
    },
    async importEntries(entries, onProgress) {
      try {
        return await importInChunks(entries, async (chunk) => (await api.bulkCreateCallListEntries(chunk)).count, BULK_LIMIT, onProgress);
      } finally {
        r.refreshEntries();
      }
    },
    async resetCompleted() {
      await api.resetCallList();
      r.refreshEntries();
    },
    async createRun(body) {
      await api.createCallRun(body);
      r.refreshRuns();
      r.refreshEntries(); // a cancelled or reached patient added to a run is back on the queue
    },
    async updateRun(run, body) {
      await api.updateCallRun(run.run_id, body);
      r.refreshRuns();
      r.refreshEntries();
    },
    async cancelRun(run) {
      await api.cancelCallRun(run.run_id);
      r.refreshRuns();
    },
    async saveRecall(body) {
      r.setSettings(await api.setRecallSettings(body));
    },
    async saveDisplay(body) {
      r.setSettings(await api.setDisplaySettings(body));
    },
    async saveOrganization(orgName) {
      r.setSettings(await api.setOrganization(orgName));
    },
    async addNumber(phone, label, smsConsent) {
      // Two separate permissions (lib/consent.ts): the allowlist authorises
      // calls; the consent record authorises texts, and is optional.
      await api.addAllowlistEntry(phone, consentLabel(label, smsConsent));
      await setSmsConsent(phone, smsConsent, CONSENT_VERSION);
      r.refreshAllowlist();
      r.refreshEntries(); // a blocked patient may now be dialable
    },
    async removeNumber(phone) {
      await api.removeAllowlistEntry(phone);
      r.refreshAllowlist();
    },
    async setConsent(phone, granted) {
      await setSmsConsent(phone, granted, CONSENT_VERSION);
      r.refreshAllowlist();
    },
    async cancelCallback(callbackId) {
      await cancelCallback(callbackId);
      r.refreshFollowUps();
    },
    async startTestCall() {
      const { softphone_url, expires_in } = await api.startTestCall();
      return { softphone_url, expires_in };
    },
    refresh: r.refresh,
  };
}

/** The admin console's on-demand reads, from the API. */
export const liveAdminReads: AdminReads = {
  async messages(phone, callSid) {
    return (await fetchMessages(phone, callSid)).messages ?? [];
  },
  callEvents: (callId) => api.getCallEvents(callId),
  async audit() {
    return (await api.getAudit(200)).entries ?? [];
  },
};

/** Sample data: every action explains itself instead of calling the API. */
export function explainSample(): Promise<void> {
  toast.info("Sample data: nothing is sent. Switch to live data to act for real.");
  return Promise.resolve();
}
