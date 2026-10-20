"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { NurseConsole } from "@/components/nurse/nurse-console";
import { demoData } from "@/lib/nurse/demo";
import { NurseActions } from "@/lib/nurse/actions";
import { api } from "@/lib/api";
import { AllowlistResponse, CallDoc, CallSettingsResponse } from "@/lib/types";
import { DEFAULT_RECALL } from "@/components/nurse/recall-settings";
import { useCallsStream } from "@/hooks/use-calls-stream";
import { useCallList } from "@/hooks/use-call-list";
import { useFollowUps } from "@/hooks/use-followups";
import { useCallRuns } from "@/hooks/use-call-runs";
import { setTimeFormat } from "@/lib/time";
import { applyOverrideLocally } from "@/lib/follow-up-reasons";
import { deleteInChunks } from "@/lib/nurse/delete-calls";

/**
 * The nurse console. The classic dashboard at / is unchanged.
 *
 *   /nurse             the real backend (default — this is a PHI surface)
 *   /nurse?data=demo   fictional sample data, clearly marked, for design work
 */
export default function NursePage() {
  return (
    <Suspense>
      <Router />
    </Suspense>
  );
}

function Router() {
  const sample = useSearchParams().get("data") === "demo";
  return sample ? <Sample /> : <Live />;
}

function Sample() {
  // State, so a sample follow-up change shows everywhere in this tab.
  const [data, setData] = useState(() => demoData());
  const [settings, setSettings] = useState<CallSettingsResponse>(
    () => ({ recall: DEFAULT_RECALL, timezone: "America/New_York", display: { time_format: "24h" } }),
  );
  useEffect(() => { setTimeFormat("24h"); }, []);
  const actions = useMemo<NurseActions>(() => {
    const explain = async () => {
      toast.info("Sample data: nothing is sent. Switch to live data to act for real.");
    };
    return {
      live: false,
      callEntries: explain,
      saveEntry: explain,
      deleteEntry: explain,
      resetCompleted: explain,
      createRun: explain,
      updateRun: explain,
      cancelRun: explain,
      saveRecall: explain,
      // Sample mode: the toggle changes the format locally so the design can be seen both ways.
      saveDisplay: async (body) => {
        setSettings((s) => ({ ...s, display: body }));
        setTimeFormat(body.time_format);
        toast.info("Sample data: time display changed in this tab only.");
      },
      cancelEntries: explain,
      rescheduleEntry: explain,
      setFollowUp: async (sid, body) => {
        await explain();
        const call = data.calls.find((c) => (c.call_sid ?? c.doc_id) === sid);
        if (!call) throw new Error("Unknown call");
        const updated = applyOverrideLocally(call, body, "you (sample)", new Date().toISOString());
        setData((d) => ({ ...d, calls: d.calls.map((c) => (c.doc_id === updated.doc_id ? updated : c)) }));
        return updated;
      },
      // Sample mode: the calls leave this tab's sample data; the API is never called.
      deleteCalls: async (callIds, onChunk) => {
        toast.info(`Sample data: nothing is sent. ${callIds.length === 1 ? "1 call" : `${callIds.length} calls`} removed from this tab only.`);
        const ids = new Set(callIds);
        setData((d) => ({ ...d, calls: d.calls.filter((c) => !ids.has(c.doc_id)) }));
        const results = callIds.map((call_sid) => ({ call_sid, status: "deleted" as const }));
        onChunk?.(results, callIds.length, callIds.length);
        return results;
      },
      refresh: () => {},
    };
  }, [data]);
  return <NurseConsole data={data} mode="demo" actions={actions} settings={settings} />;
}

function Live() {
  const { calls, loading, connState, refresh: refreshCalls } = useCallsStream();
  const { entries, run, refreshEntries, refreshRun } = useCallList();
  const { sessions, callbacks, refresh: refreshFollowUps } = useFollowUps();
  const { runs, refresh: refreshRuns } = useCallRuns();
  const { rows: openFollowUps, failed: openFailed } = useOpenFollowUps();
  const allowlist = useAllowlist();
  const [settings, setSettings] = useState<CallSettingsResponse | null>(null);
  useEffect(() => {
    api.getCallSettings().then(setSettings).catch(() => {});
  }, []);
  // Nurse-facing times follow the clinic's setting (default 24-hour).
  useEffect(() => {
    if (settings) setTimeFormat(settings.display?.time_format ?? "24h");
  }, [settings]);

  const refresh = useCallback(() => {
    refreshCalls();
    refreshEntries();
    refreshRun();
    refreshFollowUps();
    refreshRuns();
  }, [refreshCalls, refreshEntries, refreshRun, refreshFollowUps, refreshRuns]);

  // A call finishing changes the list (cleared, attempted, blocked), so
  // re-read entries whenever the call stream moves rather than waiting for
  // the idle poll.
  const callsVersion = useMemo(
    () => [...calls.values()].map((c) => c.updated_at).sort().at(-1) ?? "",
    [calls],
  );
  useEffect(() => { refreshEntries(); refreshRuns(); }, [callsVersion, refreshEntries, refreshRuns]);

  const actions = useMemo<NurseActions>(() => ({
    live: true,
    async callEntries(ids) {
      await api.startCallListRun(ids);
      refreshRun();
      refreshEntries();
    },
    async saveEntry(entry, body) {
      if (entry) await api.updateCallListEntry(entry.entry_id, body);
      else await api.createCallListEntry(body);
      refreshEntries();
    },
    async deleteEntry(id) {
      await api.deleteCallListEntry(id);
      refreshEntries();
      refreshRuns(); // the backend drops a deleted patient from their run
    },
    async createRun(body) {
      await api.createCallRun(body);
      refreshRuns();
      refreshEntries(); // a cancelled or reached patient added to a run is back on the queue
    },
    async updateRun(run, body) {
      await api.updateCallRun(run.run_id, body);
      refreshRuns();
      refreshEntries();
    },
    async cancelRun(run) {
      await api.cancelCallRun(run.run_id);
      refreshRuns();
    },
    async cancelEntries(ids, reason) {
      await api.cancelEntries(ids, reason);
      refreshEntries();
      refreshRuns(); // a cancelled patient leaves their run
    },
    async rescheduleEntry(entryId, target) {
      await api.rescheduleEntry(entryId, target);
      refreshEntries();
      refreshRuns();
    },
    async saveRecall(body) {
      setSettings(await api.setRecallSettings(body));
    },
    async saveDisplay(body) {
      setSettings(await api.setDisplaySettings(body));
    },
    async resetCompleted() {
      await api.resetCallList();
      refreshEntries();
    },
    async setFollowUp(sid, body) {
      const updated = await api.setFollowUp(sid, body);
      refreshCalls();
      return updated;
    },
    async deleteCalls(callIds, onChunk) {
      try {
        return await deleteInChunks(callIds, (part) => api.batchDelete(part), onChunk);
      } finally {
        refresh(); // callbacks, sessions and runs may have pointed at these calls
      }
    },
    refresh,
  }), [refresh, refreshCalls, refreshEntries, refreshRun, refreshRuns]);

  // The stream holds the newest 100 calls; an open follow-up older than that
  // would vanish from Needs-you lists (K3). Merge the server's open list in.
  const data = useMemo(() => {
    const recent = [...calls.values()];
    const seen = new Set(recent.map((c) => c.doc_id));
    return { calls: [...recent, ...openFollowUps.filter((c) => !seen.has(c.doc_id))], entries, runs, sessions, callbacks };
  }, [calls, openFollowUps, entries, runs, sessions, callbacks]);
  return (
    <NurseConsole
      data={data}
      mode="live"
      loading={loading}
      actions={actions}
      running={run?.status === "running"}
      allowlist={allowlist}
      settings={settings}
      unavailable={[
        ...(openFailed ? ["open follow-ups"] : []),
        ...(!loading && connState === "disconnected" ? ["live call updates"] : []),
      ]}
    />
  );
}

/** Open follow-ups from the whole collection, re-read every 30s. `failed`
 *  is true while the last read failed: the lists may then be missing an
 *  older urgent patient, and the console says so instead of looking clear
 *  (B-9). The last good rows are kept meanwhile. */
function useOpenFollowUps(): { rows: CallDoc[]; failed: boolean } {
  const [rows, setRows] = useState<CallDoc[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => api.getOpenFollowUps()
      .then((r) => { if (alive) { setRows(r.calls); setFailed(false); } })
      .catch(() => { if (alive) setFailed(true); });
    load();
    const id = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return { rows, failed };
}

/** The outbound allowlist, re-read every 30s; null until first loaded. */
function useAllowlist(): AllowlistResponse | null {
  const [allowlist, setAllowlist] = useState<AllowlistResponse | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.getAllowlist().then((a) => alive && setAllowlist(a)).catch(() => {});
    load();
    const id = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return allowlist;
}
