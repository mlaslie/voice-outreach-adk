"use client";

import { useState } from "react";
import { toast } from "sonner";
import { CalendarClock, CalendarPlus, CheckCircle2, Pencil, PhoneCall, Plus, RotateCcw, ShieldAlert, ShieldCheck, Trash2, X } from "lucide-react";
import Link from "next/link";
import { AllowlistResponse, CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { NurseActions } from "@/lib/nurse/actions";
import { isAllowed } from "@/lib/nurse/allowlist";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, Panel, Pill, Tone, clock, countdown } from "./parts";
import { EntryDialog } from "./entry-dialog";
import { RunDialog } from "./run-dialog";
import { DEFAULT_RECALL, RecallSettingsPanel } from "./recall-settings";
import { inCallingWindow } from "@/lib/nurse/window";

/**
 * Demo administration: the patients the agent will call, with every field
 * the agent needs and a planned time. Hidden under Settings because a nurse
 * does not normally enter patients — in a real deployment this list comes
 * from the EHR.
 */
export function SettingsView({
  entries,
  runs,
  actions,
  now,
  allowlist,
  settings,
}: {
  entries: CallListEntry[];
  runs: CallRun[];
  actions: NurseActions;
  now: Date;
  allowlist?: AllowlistResponse | null;
  settings?: CallSettingsResponse | null;
}) {
  const [dialog, setDialog] = useState<{ open: boolean; entry: CallListEntry | null }>({ open: false, entry: null });
  const [runDialog, setRunDialog] = useState<{ open: boolean; run: CallRun | null }>({ open: false, run: null });
  const [busy, setBusy] = useState<string | null>(null);

  // The scheduled run each patient is in (at most one; the backend enforces it).
  const runOf = new Map<string, CallRun>();
  for (const r of runs) if (r.status === "scheduled") for (const id of r.entry_ids) runOf.set(id, r);

  const cancelRun = async (r: CallRun) => {
    const when = new Date(r.scheduled_for).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
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

  const remove = async (e: CallListEntry) => {
    if (!window.confirm(`Remove ${e.patient_name} from the call list?`)) return;
    setBusy(e.entry_id);
    try {
      await actions.deleteEntry(e.entry_id);
      if (actions.live) toast.success(`Removed ${e.patient_name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove the patient.");
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    const done = entries.filter((e) => e.list_status === "completed").length;
    if (!done) return;
    if (!window.confirm(`Put ${done} completed ${done === 1 ? "patient" : "patients"} back on the queue? They will be called again on the next run.`)) return;
    setBusy("reset");
    try {
      await actions.resetCompleted();
      if (actions.live) toast.success("Completed patients are pending again");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not reset the list.");
    } finally {
      setBusy(null);
    }
  };

  const callNow = async (e: CallListEntry) => {
    if (!window.confirm(`Call ${e.patient_name} now? This places a real call through the automated agent.`)) return;
    setBusy(e.entry_id);
    try {
      await actions.callEntries([e.entry_id]);
      if (actions.live) toast.success(`Calling ${e.patient_name}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not start the call.");
    } finally {
      setBusy(null);
    }
  };

  const sorted = [...entries].sort((a, b) => a.patient_name.localeCompare(b.patient_name));
  const nameOf = new Map(entries.map((e) => [e.entry_id, e.patient_name]));
  const scheduledRuns = runs.filter((r) => r.status !== "dispatched").sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
  const recentRuns = runs.filter((r) => r.status === "dispatched").sort((a, b) => b.scheduled_for.localeCompare(a.scheduled_for)).slice(0, 5);

  return (
    <div className="space-y-5">
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
                        {new Date(r.scheduled_for).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} · {clock(r.scheduled_for)}
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
                    {new Date(r.scheduled_for).toLocaleDateString(undefined, { month: "short", day: "numeric" })} {clock(r.scheduled_for)}
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

      <RecallSettingsPanel settings={settings?.recall ?? null} timezone={settings?.timezone} actions={actions} />

      {/* ---- Patients ---- */}
      <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
        <div className="text-[13px] text-ink-soft">
          <span className="font-semibold text-ink">{entries.length}</span> on the list ·{" "}
          {entries.filter((e) => e.list_status === "pending").length} pending ·{" "}
          {entries.filter((e) => e.list_status === "completed").length} completed
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button
            onClick={reset}
            disabled={busy !== null || !entries.some((e) => e.list_status === "completed")}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-40"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset completed
          </button>
          <button
            onClick={() => setDialog({ open: true, entry: null })}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-brand-dark"
          >
            <Plus className="h-3.5 w-3.5" /> Add patient
          </button>
        </div>
      </Panel>

      <Panel className="overflow-hidden">
        {sorted.length === 0 ? (
          <EmptyState title="No patients on the list" hint="Add a patient with the details the agent needs and, optionally, a planned call time." />
        ) : (
          <table className="w-full text-left text-[13px]">
            <thead className="bg-mist/60 text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-muted">
              <tr>
                <th className="px-5 py-3">Patient</th>
                <th className="hidden px-3 py-3 md:table-cell">Procedure</th>
                <th className="hidden px-3 py-3 lg:table-cell">DOB</th>
                <th className="px-3 py-3">Run</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.05]">
              {sorted.map((e) => (
                <tr key={e.entry_id} className={cn(busy === e.entry_id && "opacity-50")}>
                  <td className="px-5 py-3">
                    <div className="font-display font-semibold text-ink">{e.patient_name}</div>
                    <div className="text-[12px] text-ink-muted">
                      {e.patient_id} · <span className="tabular-nums">{formatPhone(e.phone)}</span>
                      {e.is_diabetic ? " · diabetic" : ""}
                    </div>
                  </td>
                  <td className="hidden px-3 py-3 text-ink-soft md:table-cell">
                    {e.operation_name || "—"}{e.operation_date ? <span className="text-ink-muted"> · {e.operation_date}</span> : null}
                  </td>
                  <td className="hidden px-3 py-3 tabular-nums text-ink-soft lg:table-cell">{e.patient_dob}</td>
                  <td className="px-3 py-3">
                    {runOf.get(e.entry_id) ? (
                      <span className="inline-flex items-center gap-1.5 tabular-nums text-ink">
                        <CalendarClock className="h-3.5 w-3.5" />
                        {new Date(runOf.get(e.entry_id)!.scheduled_for).toLocaleDateString(undefined, { month: "short", day: "numeric" })} {clock(runOf.get(e.entry_id)!.scheduled_for)}
                      </span>
                    ) : (
                      <span className="text-ink-muted" title="Not in a scheduled run. Add them to one, or call by hand.">Not in a run</span>
                    )}
                  </td>
                  <td className="px-3 py-3"><StatusPill entry={e} allowed={isAllowed(e.phone, allowlist)} /></td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-1">
                      {e.list_status !== "calling" ? (
                        <button
                          onClick={() => callNow(e)}
                          disabled={busy !== null}
                          className="grid h-8 w-8 place-items-center rounded-lg text-ink-soft hover:bg-brand/[0.08] hover:text-brand-dark"
                          aria-label={`Call ${e.patient_name} now`}
                          title="Call now"
                        >
                          <PhoneCall className="h-4 w-4" />
                        </button>
                      ) : null}
                      <button
                        onClick={() => setDialog({ open: true, entry: e })}
                        className="grid h-8 w-8 place-items-center rounded-lg text-ink-soft hover:bg-brand/[0.08] hover:text-brand-dark"
                        aria-label={`Edit ${e.patient_name}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => remove(e)}
                        disabled={busy !== null}
                        className="grid h-8 w-8 place-items-center rounded-lg text-ink-soft hover:bg-brand-red/[0.08] hover:text-brand-red-dark"
                        aria-label={`Remove ${e.patient_name}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {allowlist ? (
        <Panel className="flex flex-wrap items-center gap-3 p-5 text-[13px] text-ink-soft">
          {allowlist.unrestricted ? <ShieldCheck className="h-4 w-4 text-brand" /> : <ShieldAlert className="h-4 w-4 text-brand-orange" />}
          <span>
            <span className="font-semibold text-ink">Approved numbers.</span>{" "}
            {allowlist.unrestricted
              ? "Any number may be dialled on this deployment."
              : `The agent only dials numbers on the approved list (${allowlist.count}). A patient whose number is not on it is refused at dial time and marked blocked.`}
          </span>
          <Link href="/" className="ml-auto rounded-full bg-white px-3.5 py-1.5 font-semibold text-brand-deep ring-1 ring-black/[0.08] hover:bg-brand/[0.06]">
            Manage approved numbers
          </Link>
        </Panel>
      ) : null}

      <Panel className="p-5 text-[13px] text-ink-soft">
        <h3 className="font-display text-[14px] font-semibold text-ink">How the planned time is used</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>A call run is a date and time plus the patients to call then. Every scheduled run and its patients appear on the call queue.</li>
          <li>A Cloud Scheduler job checks every minute. When a run’s time arrives it calls the run’s patients who are still pending, once. Anyone not reached gets the usual text follow-up.</li>
          <li>A patient can be in only one scheduled run at a time. Removing the last patient from a run removes the run.</li>
          <li>Auto-recall: a patient a run did not reach, who has not answered the follow-up text, is put in an automatic run after the interval above, inside the allowed hours and days. A nurse-scheduled run resets the count.</li>
          <li>A patient who is not in a run is never called automatically; use the phone button to call them by hand. A nurse can also call anyone from the queue at any moment.</li>
          <li>Only a completed questionnaire (by phone or by text) clears a patient from the list.</li>
        </ul>
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
      <EntryDialog
        open={dialog.open}
        entry={dialog.entry}
        actions={actions}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
      />
    </div>
  );
}

const STATUS: Record<CallListEntry["list_status"], { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "info" },
  calling: { label: "Calling now", tone: "good" },
  completed: { label: "Completed", tone: "good" },
  blocked: { label: "Blocked", tone: "warn" },
  canceled: { label: "Cancelled", tone: "warn" },
};

function StatusPill({ entry, allowed }: { entry: CallListEntry; allowed?: boolean }) {
  const s = STATUS[entry.list_status] ?? { label: entry.list_status, tone: "muted" as Tone };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Pill tone={s.tone} pulse={entry.list_status === "calling"}>{s.label}</Pill>
      {allowed === false ? <Pill tone="warn" icon={ShieldAlert}>Not an approved number</Pill> : null}
      {entry.attempts > 0 ? <span className="text-[11px] text-ink-muted">{entry.attempts} {entry.attempts === 1 ? "attempt" : "attempts"}{entry.last_call_status ? ` · ${entry.last_call_status}` : ""}</span> : null}
      {entry.last_error ? <span className="text-[11px] text-brand-coral-dark">{entry.last_error}</span> : null}
    </div>
  );
}
