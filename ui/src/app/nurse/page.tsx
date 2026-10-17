"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { NurseConsole } from "@/components/nurse/nurse-console";
import { demoData } from "@/lib/nurse/demo";
import { NurseActions } from "@/lib/nurse/actions";
import { api } from "@/lib/api";
import { AllowlistResponse, CallSettingsResponse } from "@/lib/types";
import { DEFAULT_RECALL } from "@/components/nurse/recall-settings";
import { useCallsStream } from "@/hooks/use-calls-stream";
import { useCallList } from "@/hooks/use-call-list";
import { useFollowUps } from "@/hooks/use-followups";
import { useCallRuns } from "@/hooks/use-call-runs";

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
  const data = useMemo(() => demoData(), []);
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
      cancelEntries: explain,
      rescheduleEntry: explain,
      setFollowUp: async (sid, body) => {
        await explain();
        const call = data.calls.find((c) => (c.call_sid ?? c.doc_id) === sid);
        if (!call) throw new Error("Unknown call");
        const event = { at: new Date().toISOString(), from: call.follow_up ?? null, to: body.follow_up, reason: body.reason, set_by: "you (sample)" };
        return { ...call, follow_up: body.follow_up, agent_follow_up: call.agent_follow_up ?? call.follow_up ?? null,
          follow_up_override: event, follow_up_history: [...(call.follow_up_history ?? []), event] };
      },
      refresh: () => {},
    };
  }, [data]);
  const settings = useMemo<CallSettingsResponse>(() => ({ recall: DEFAULT_RECALL, timezone: "America/New_York" }), []);
  return <NurseConsole data={data} mode="demo" actions={actions} settings={settings} />;
}

function Live() {
  const { calls, loading, refresh: refreshCalls } = useCallsStream();
  const { entries, run, refreshEntries, refreshRun } = useCallList();
  const { sessions, callbacks, refresh: refreshFollowUps } = useFollowUps();
  const { runs, refresh: refreshRuns } = useCallRuns();
  const allowlist = useAllowlist();
  const [settings, setSettings] = useState<CallSettingsResponse | null>(null);
  useEffect(() => {
    api.getCallSettings().then(setSettings).catch(() => {});
  }, []);

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
    },
    async updateRun(run, body) {
      await api.updateCallRun(run.run_id, body);
      refreshRuns();
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
    async resetCompleted() {
      await api.resetCallList();
      refreshEntries();
    },
    async setFollowUp(sid, body) {
      const updated = await api.setFollowUp(sid, body);
      refreshCalls();
      return updated;
    },
    refresh,
  }), [refresh, refreshCalls, refreshEntries, refreshRun, refreshRuns]);

  const data = useMemo(
    () => ({ calls: [...calls.values()], entries, runs, sessions, callbacks }),
    [calls, entries, runs, sessions, callbacks],
  );
  return (
    <NurseConsole
      data={data}
      mode="live"
      loading={loading}
      actions={actions}
      running={run?.status === "running"}
      allowlist={allowlist}
      settings={settings}
    />
  );
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
