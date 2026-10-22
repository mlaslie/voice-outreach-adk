"use client";

import { LanguagesProvider } from "@/components/languages-provider";
import { useMounted } from "@/hooks/use-mounted";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { AdminConsole } from "@/components/admin/admin-console";
import { ConsentMap } from "@/components/admin/allowlist-panel";
import { DEFAULT_RECALL } from "@/components/nurse/recall-settings";
import { demoAllowlist, demoData } from "@/lib/nurse/demo";
import { demoAdminReads, demoAdminSessions } from "@/lib/admin-demo";
import { AdminActions } from "@/lib/nurse/actions";
import { explainSample, liveAdminActions, liveAdminReads } from "@/lib/console-actions";
import { consentLabel } from "@/lib/consent";
import { api, ApiError } from "@/lib/api";
import { ScheduledCallback, fetchSmsConsent } from "@/lib/followups";
import { AllowlistResponse, CallDoc, CallSettingsResponse } from "@/lib/types";
import { useCallList } from "@/hooks/use-call-list";
import { useCallRuns } from "@/hooks/use-call-runs";
import { useFollowUps } from "@/hooks/use-followups";
import { setTimeFormat } from "@/lib/time";

/**
 * The admin console (client side; page.tsx passes the runtime link in).
 *
 *   /admin             the real backend (default)
 *   /admin?data=demo   the nurse console's fictional sample data, plus
 *                      sample texts, a fictional call timeline and audit
 *                      entries (lib/admin-demo.ts); no action calls the API
 */
export function AdminApp({ nurseConsoleUrl }: { nurseConsoleUrl: string | null }) {
  return (
    <Suspense>
      <Router nurseConsoleUrl={nurseConsoleUrl} />
    </Suspense>
  );
}

function Router({ nurseConsoleUrl }: { nurseConsoleUrl: string | null }) {
  const sample = useSearchParams().get("data") === "demo";
  const mounted = useMounted();
  if (sample && !mounted) return null; // sample data is built in the browser only
  // Patient languages (GET /api/languages) for every form, list and chip below.
  return (
    <LanguagesProvider mode={sample ? "demo" : "live"}>
      {sample ? <Sample nurseConsoleUrl={nurseConsoleUrl} /> : <Live nurseConsoleUrl={nurseConsoleUrl} />}
    </LanguagesProvider>
  );
}

function Sample({ nurseConsoleUrl }: { nurseConsoleUrl: string | null }) {
  const [data] = useState(() => demoData());
  // Imported sample patients join this tab's list only.
  const [entries, setEntries] = useState(() => data.entries);
  const [settings, setSettings] = useState<CallSettingsResponse>(
    () => ({
      recall: DEFAULT_RECALL,
      timezone: "America/New_York",
      display: { time_format: "24h" },
      organization: { org_name: "Acme Health", default: "Acme Health", is_default: true },
    }),
  );
  // Sample numbers change in this tab only, so the page can be tried out.
  const [allowlist, setAllowlist] = useState<AllowlistResponse>(() => demoAllowlist(data.entries).allowlist);
  const [consent, setConsent] = useState<ConsentMap>(() => demoAllowlist(data.entries).consent);
  const [sessions] = useState(() => demoAdminSessions(data));
  const [callbacks, setCallbacks] = useState<ScheduledCallback[]>(() => data.callbacks);
  const reads = useMemo(() => demoAdminReads(sessions, data.calls), [sessions, data.calls]);
  useEffect(() => { setTimeFormat("24h"); }, []);

  const actions = useMemo<AdminActions>(() => {
    const explain = explainSample;
    const localOnly = (what: string) => toast.info(`Sample data: nothing is sent. ${what} in this tab only.`);
    return {
      live: false,
      saveEntry: explain,
      deleteEntry: explain,
      importEntries: async (rows, onProgress) => {
        const at = new Date().toISOString();
        setEntries((list) => [
          ...list,
          ...rows.map((e, i) => ({
            ...e,
            entry_id: `sample${Date.now().toString(16)}${String(i).padStart(6, "0")}`,
            list_status: "pending" as const,
            attempts: 0,
            created_at: at,
            updated_at: at,
          })),
        ]);
        onProgress?.(rows.length, rows.length);
        localOnly(`${rows.length === 1 ? "1 patient" : `${rows.length} patients`} added`);
        return { added: rows.length, rejected: [] };
      },
      resetCompleted: explain,
      createRun: explain,
      updateRun: explain,
      cancelRun: explain,
      saveRecall: explain,
      // The toggle changes the format locally so the design can be seen both ways.
      saveOrganization: async (orgName) => {
        setSettings((s) => {
          const def = s.organization?.default ?? "Acme Health";
          const name = orgName.trim() || def;
          return { ...s, organization: { org_name: name, default: def, is_default: name === def } };
        });
        localOnly("Company name changed");
      },
      saveDisplay: async (body) => {
        setSettings((s) => ({ ...s, display: body }));
        setTimeFormat(body.time_format);
        localOnly("Time display changed");
      },
      addNumber: async (phone, label, smsConsent) => {
        setAllowlist((a) => {
          const entries = [
            ...a.entries.filter((e) => e.phone !== phone),
            { phone, label: consentLabel(label, smsConsent), source: "firestore" as const, editable: true },
          ];
          return { ...a, entries, count: entries.length };
        });
        setConsent((c) => ({ ...c, [phone]: smsConsent }));
        localOnly("Number added");
      },
      removeNumber: async (phone) => {
        setAllowlist((a) => {
          const entries = a.entries.filter((e) => e.phone !== phone);
          return { ...a, entries, count: entries.length };
        });
        localOnly("Number removed");
      },
      setConsent: async (phone, granted) => {
        setConsent((c) => ({ ...c, [phone]: granted }));
        localOnly("Consent changed");
      },
      cancelCallback: async (callbackId) => {
        setCallbacks((cbs) => cbs.map((c) => (c.callback_id === callbackId ? { ...c, status: "canceled" as const } : c)));
        localOnly("Callback cancelled");
      },
      startTestCall: async () => {
        toast.info("Sample data: nothing is sent. A browser test call runs against the live backend only; switch to live data to try it.");
        return null;
      },
      refresh: () => {},
    };
  }, []);

  return (
    <AdminConsole
      data={{ entries, runs: data.runs, sessions, callbacks, calls: data.calls }}
      mode="demo"
      actions={actions}
      reads={reads}
      allowlist={allowlist}
      consent={consent}
      settings={settings}
      nurseConsoleUrl={nurseConsoleUrl}
    />
  );
}

function Live({ nurseConsoleUrl }: { nurseConsoleUrl: string | null }) {
  const { entries, loading, error: listError, refreshEntries } = useCallList();
  const { runs, refresh: refreshRuns } = useCallRuns();
  const { allowlist, error: allowlistError, refresh: refreshAllowlist } = useAllowlist();
  const consent = useConsent(allowlist);
  const { sessions, callbacks, error: followUpsError, refresh: refreshFollowUps } = useFollowUps();
  const { calls, error: callsError, refresh: refreshCalls } = useRecentCalls();
  const [settings, setSettings] = useState<CallSettingsResponse | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);

  const loadSettings = useCallback(() => {
    api.getCallSettings()
      .then((s) => { setSettings(s); setSettingsFailed(false); })
      .catch(() => setSettingsFailed(true));
  }, []);
  useEffect(() => { loadSettings(); }, [loadSettings]);
  useEffect(() => {
    if (settings) setTimeFormat(settings.display?.time_format ?? "24h");
  }, [settings]);

  const refresh = useCallback(() => {
    refreshEntries();
    refreshRuns();
    refreshAllowlist();
    loadSettings();
    refreshFollowUps();
    refreshCalls();
  }, [refreshEntries, refreshRuns, refreshAllowlist, loadSettings, refreshFollowUps, refreshCalls]);

  const actions = useMemo<AdminActions>(
    () => liveAdminActions({ refresh, refreshEntries, refreshRuns, refreshAllowlist, refreshFollowUps, setSettings }),
    [refresh, refreshEntries, refreshRuns, refreshAllowlist, refreshFollowUps],
  );

  return (
    <AdminConsole
      data={{ entries, runs, sessions, callbacks, calls }}
      mode="live"
      loading={loading}
      actions={actions}
      reads={liveAdminReads}
      followUpsError={followUpsError}
      callsError={callsError}
      onRefreshCalls={refreshCalls}
      allowlist={allowlist}
      allowlistError={allowlistError}
      consent={consent}
      settings={settings}
      nurseConsoleUrl={nurseConsoleUrl}
      unavailable={[
        ...(listError ? ["the patient list"] : []),
        ...(settingsFailed ? ["the calling hours"] : []),
      ]}
    />
  );
}

/** The newest calls (Call diagnostics), re-read every 30 s; the last good list is kept on a failure. */
function useRecentCalls() {
  const [calls, setCalls] = useState<CallDoc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => api.getCalls({ limit: 200 })
      .then((r) => { if (alive) { setCalls(r.calls ?? []); setError(null); } })
      .catch((e) => { if (alive) setError(e instanceof ApiError ? e.message : "Could not load recent calls"); });
    load();
    const id = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(id); };
  }, [n]);
  const refresh = useCallback(() => setN((x) => x + 1), []);
  return { calls, error, refresh };
}

/** The outbound allowlist; `refresh` re-reads it after a change. */
function useAllowlist() {
  const [allowlist, setAllowlist] = useState<AllowlistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => api.getAllowlist()
      .then((a) => { if (alive) { setAllowlist(a); setError(null); } })
      .catch((e) => { if (alive) setError(e instanceof ApiError ? e.message : "Could not load the allowlist"); });
    load();
    const id = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(id); };
  }, [n]);
  const refresh = useCallback(() => setN((x) => x + 1), []);
  return { allowlist, error, refresh };
}

/** Text consent for each editable number, re-read whenever the list is. */
function useConsent(allowlist: AllowlistResponse | null): ConsentMap {
  const [consent, setConsent] = useState<ConsentMap>({});
  useEffect(() => {
    if (!allowlist) return;
    let alive = true;
    const phones = allowlist.entries.filter((e) => e.editable).map((e) => e.phone);
    Promise.allSettled(phones.map((p) => fetchSmsConsent(p))).then((results) => {
      if (!alive) return;
      const next: ConsentMap = {};
      results.forEach((r, i) => { next[phones[i]] = r.status === "fulfilled" ? r.value.granted : undefined; });
      setConsent(next);
    });
    return () => { alive = false; };
  }, [allowlist]);
  return consent;
}
