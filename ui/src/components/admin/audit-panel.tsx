"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { AdminReads } from "@/lib/nurse/actions";
import { AuditEntry } from "@/lib/types";
import { describeAction, describeTarget } from "@/lib/audit";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { EmptyState, Panel } from "@/components/nurse/parts";

/**
 * Admin → Audit log: every configuration change, newest first. Written by
 * the API after each successful change (who, when, which route, which
 * record); request bodies are never stored.
 */
export function AuditPanel({ reads, timeZone }: { reads: Pick<AdminReads, "audit">; timeZone?: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setEntries(await reads.audit());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the audit log.");
    } finally {
      setBusy(false);
    }
  }, [reads]);
  useEffect(() => { load(); }, [load]);

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <ScrollText className="h-4 w-4 text-brand" />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Configuration changes</h3>
          <p className="text-[12px] text-ink-muted">Who changed what, newest first. Times are clinic time.</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} /> Refresh
        </button>
      </div>
      {error ? (
        <p className="border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark">
          {error}{entries ? " Showing the last list loaded." : ""}
        </p>
      ) : null}
      {entries === null ? (
        <p className="px-5 py-6 text-[13px] text-ink-muted">{error ? "Could not load the audit log." : "Loading…"}</p>
      ) : entries.length === 0 ? (
        <EmptyState title="No changes recorded yet" hint="Each change made in this console is listed here with who made it." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-black/[0.05] text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                <th className="px-5 py-2.5 font-semibold">When</th>
                <th className="px-3 py-2.5 font-semibold">Who</th>
                <th className="px-3 py-2.5 font-semibold">What</th>
                <th className="px-5 py-2.5 font-semibold">Target</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.05]">
              {entries.map((e) => {
                const what = describeAction(e.action);
                return (
                  <tr key={e.id} className="align-top">
                    <td className="whitespace-nowrap px-5 py-2.5 tabular-nums text-ink-soft">
                      {fmtDateTime(e.at, { weekday: "short", month: "short", day: "numeric", timeZone })}
                    </td>
                    <td className="max-w-[16rem] truncate px-3 py-2.5 text-ink" title={e.by ?? undefined}>{e.by || "A service"}</td>
                    <td className="px-3 py-2.5">
                      <span className="font-semibold text-ink">{what}</span>
                      {what !== e.action ? <span className="block font-mono text-[11px] text-ink-muted">{e.action}</span> : null}
                    </td>
                    <td className="px-5 py-2.5 tabular-nums text-ink-soft">{describeTarget(e.target) || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
