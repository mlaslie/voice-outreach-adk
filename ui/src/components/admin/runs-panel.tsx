"use client";

import { useState } from "react";
import { toast } from "sonner";
import { CalendarClock, CalendarPlus, CheckCircle2, Pencil, Trash2, X } from "lucide-react";
import { CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { AdminActions } from "@/lib/nurse/actions";
import { cn } from "@/lib/utils";
import { Panel, Pill, countdown } from "@/components/nurse/parts";
import { fmtDateTime } from "@/lib/time";
import { RunDialog } from "@/components/nurse/run-dialog";
import { DEFAULT_RECALL } from "@/components/nurse/recall-settings";
import { inCallingWindow } from "@/lib/nurse/window";

/**
 * Admin → Schedules: the scheduled call runs (create, change, cancel) and
 * the recently dialled ones. Moved from the nurse console's Settings.
 */
export function RunsPanel({
  entries,
  runs,
  actions,
  now,
  settings,
}: {
  entries: CallListEntry[];
  runs: CallRun[];
  actions: AdminActions;
  now: Date;
  settings?: CallSettingsResponse | null;
}) {
  const [runDialog, setRunDialog] = useState<{ open: boolean; run: CallRun | null }>({ open: false, run: null });
  const [busy, setBusy] = useState<string | null>(null);

  const cancelRun = async (r: CallRun) => {
    const when = fmtDateTime(r.scheduled_for, { weekday: "short", month: "short", day: "numeric" });
    if (!window.confirm(`Cancel the ${when} run (${r.entry_ids.length} ${r.entry_ids.length === 1 ? "patient" : "patients"})? Nobody in it will be called.`)) return;
    setBusy(r.run_id);
    try {
      await actions.cancelRun(r);
      if (actions.live) toast.success("Run cancelled");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not cancel the run.");
    } finally {
      setBusy(null);
    }
  };

  const removeFromRun = async (r: CallRun, entryId: string, name: string) => {
    const remaining = r.entry_ids.filter((id) => id !== entryId);
    if (!remaining.length && !window.confirm(`${name} is the only patient in this run. Removing them removes the run. Continue?`)) return;
    setBusy(r.run_id);
    try {
      await actions.updateRun(r, { entry_ids: remaining });
      if (actions.live) toast.success(remaining.length ? `Removed ${name} from the run` : "Run removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not change the run.");
    } finally {
      setBusy(null);
    }
  };

  const nameOf = new Map(entries.map((e) => [e.entry_id, e.patient_name]));
  const scheduledRuns = runs.filter((r) => r.status !== "dispatched").sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
  const recentRuns = runs.filter((r) => r.status === "dispatched").sort((a, b) => b.scheduled_for.localeCompare(a.scheduled_for)).slice(0, 5);

  return (
    <>
      {/* ---- Call runs ---- */}
      <Panel className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
          <div>
            <h3 className="font-display text-[15px] font-semibold text-ink">Call runs</h3>
            <p className="text-[12px] text-ink-muted">
              Each run calls its patients at the chosen time. The scheduler checks every minute.
            </p>
          </div>
          <button
            onClick={() => setRunDialog({ open: true, run: null })}
            disabled={!entries.some((e) => e.list_status === "pending")}
            className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-brand-dark disabled:opacity-40"
          >
            <CalendarPlus className="h-3.5 w-3.5" /> New run
          </button>
        </div>
        {scheduledRuns.length === 0 ? (
          <p className="px-5 py-6 text-[13px] text-ink-muted">
            No runs scheduled. Create one to put patients on the call queue.
          </p>
        ) : (
          <ul className="divide-y divide-black/[0.05]">
            {scheduledRuns.map((r) => {
              const late = r.status === "scheduled" && new Date(r.scheduled_for).getTime() + 3 * 60_000 < now.getTime();
              return (
                <li key={r.run_id} className={cn("flex flex-wrap items-start gap-3 px-5 py-3.5", busy === r.run_id && "opacity-50")}>
                  <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-brand/10 text-brand-dark">
                    <CalendarClock className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-display text-[15px] font-semibold tabular-nums text-ink">
                        {fmtDateTime(r.scheduled_for, { weekday: "short", month: "short", day: "numeric", sep: " · " })}
                      </span>
                      {r.label ? <span className="text-[13px] font-semibold text-ink-soft">{r.label}</span> : null}
                      {r.auto_recall ? <Pill tone="teal">Automatic</Pill> : null}
                      {!inCallingWindow(r.scheduled_for, settings?.recall ?? DEFAULT_RECALL, settings?.timezone ?? "America/New_York")
                        ? <Pill tone="warn">Outside calling hours</Pill> : null}
                      {r.status === "dispatching" ? <Pill tone="good" pulse>Starting</Pill>
                        : late ? <Pill tone="urgent">Scheduler has not started it</Pill>
                        : <Pill tone="muted">{countdown(r.scheduled_for, now).text}</Pill>}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {r.entry_ids.map((id) => (
                        <span key={id} className="inline-flex items-center gap-1 rounded-full bg-mist px-2.5 py-1 text-[12px] text-ink">
                          {nameOf.get(id) ?? "Removed patient"}
                          {r.status === "scheduled" ? (
                            <button
                              onClick={() => removeFromRun(r, id, nameOf.get(id) ?? "this patient")}
                              disabled={busy !== null}
                              aria-label={`Remove ${nameOf.get(id) ?? "patient"} from this run`}
                              className="-mr-1 grid h-4 w-4 place-items-center rounded-full text-ink-muted hover:bg-brand-red/10 hover:text-brand-red-dark"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          ) : null}
                        </span>
                      ))}
                    </div>
                  </div>
                  {r.status === "scheduled" ? (
                    <div className="flex gap-1">
                      <button
                        onClick={() => setRunDialog({ open: true, run: r })}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-semibold text-brand-dark ring-1 ring-black/[0.08] hover:bg-brand/[0.06]"
                      >
                        <Pencil className="h-3.5 w-3.5" /> Change
                      </button>
                      <button
                        onClick={() => cancelRun(r)}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-semibold text-brand-red-dark ring-1 ring-black/[0.08] hover:bg-brand-red/[0.06]"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Cancel run
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {recentRuns.length ? (
          <details className="border-t border-black/[0.05] px-5 py-3 text-[12px] text-ink-soft">
            <summary className="cursor-pointer font-semibold">Recently dialled runs ({recentRuns.length})</summary>
            <ul className="mt-2 space-y-1">
              {recentRuns.map((r) => (
                <li key={r.run_id} className="flex flex-wrap items-center gap-2">
                  <CheckCircle2 className="h-3.5 w-3.5 text-brand" />
                  <span className="tabular-nums text-ink">
                    {fmtDateTime(r.scheduled_for, { month: "short", day: "numeric", sep: " " })}
                  </span>
                  {r.label ? <span>{r.label}</span> : null}
                  <span>· dialled {r.dialled ?? 0} of {r.entry_ids.length}</span>
                  {r.skipped_replied?.length ? <span>· {r.skipped_replied.length} skipped, answered the text</span> : null}
                  {r.note ? <span className="text-ink-muted">· {r.note}</span> : null}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </Panel>

      <RunDialog
        open={runDialog.open}
        run={runDialog.run}
        runs={runs}
        entries={entries}
        actions={actions}
        settings={settings}
        onOpenChange={(open) => setRunDialog((d) => ({ ...d, open }))}
      />
    </>
  );
}
