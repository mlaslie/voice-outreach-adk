"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { CalendarClock, Download, FileUp, Languages, Pencil, Plus, RotateCcw, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import { AllowlistResponse, CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { AdminActions } from "@/lib/nurse/actions";
import { isAllowed } from "@/lib/nurse/allowlist";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, Panel, Pill, Tone } from "@/components/nurse/parts";
import { fmtDateTime } from "@/lib/time";
import { EntryDialog } from "@/components/nurse/entry-dialog";
import { CsvError, CsvPreview, downloadExampleCsv, previewCallListCsv } from "@/lib/call-list-csv";
import { ImportDialog } from "./import-dialog";
import { useLanguages } from "@/components/languages-provider";
import { LanguageChip } from "@/components/language-chip";
import { holdReason } from "@/lib/languages";

/**
 * Admin → Patients: the call list (add, edit, delete, reset completed), with
 * every field the agent needs and the run each patient is in. Moved from the
 * nurse console's Settings; in a real deployment this list comes from the
 * EHR. Calling someone now stays with the nurses (the call queue).
 */
export function PatientsPanel({
  entries,
  runs,
  actions,
  allowlist,
  settings,
  onManageNumbers,
}: {
  entries: CallListEntry[];
  runs: CallRun[];
  actions: AdminActions;
  allowlist?: AllowlistResponse | null;
  settings?: CallSettingsResponse | null;
  /** Open Authorized numbers. */
  onManageNumbers: () => void;
}) {
  const [dialog, setDialog] = useState<{ open: boolean; entry: CallListEntry | null }>({ open: false, entry: null });
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const langs = useLanguages();
  const [imported, setImported] = useState<{ name: string; preview: CsvPreview } | null>(null);

  // Read and check the whole file in the browser; nothing is sent until the
  // admin has seen every row (ImportDialog).
  const pickFile = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = ""; // so picking the same file again fires onChange
    if (!file) return;
    try {
      const preview = previewCallListCsv(await file.text(), entries.map((e) => e.patient_id), langs?.languages ?? null);
      setImported({ name: file.name, preview });
    } catch (err) {
      toast.error(err instanceof CsvError ? err.message : "Could not read that file.");
    }
  };

  // The scheduled run each patient is in (at most one; the backend enforces it).
  const runOf = new Map<string, CallRun>();
  for (const r of runs) if (r.status === "scheduled") for (const id of r.entry_ids) runOf.set(id, r);

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

  const sorted = [...entries].sort((a, b) => a.patient_name.localeCompare(b.patient_name));

  return (
    <>
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
            onClick={downloadExampleCsv}
            className="inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-semibold text-ink-soft hover:text-ink"
          >
            <Download className="h-3.5 w-3.5" /> Example CSV
          </button>
          <button
            onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink"
          >
            <FileUp className="h-3.5 w-3.5" /> Import CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={pickFile} aria-hidden tabIndex={-1} />
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
          <EmptyState title="No patients on the list" hint="Add a patient with the details the agent needs and, optionally, a planned call time, or import a CSV. The example CSV shows the columns." />
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
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-display font-semibold text-ink">{e.patient_name}</span>
                      <LanguageChip code={e.language} />
                    </div>
                    <div className="text-[12px] text-ink-muted">
                      {e.patient_id} · <span className="tabular-nums">{formatPhone(e.phone)}</span>
                      {e.is_diabetic ? " · diabetic" : ""}
                      {e.care_setting === "inpatient" ? " · inpatient" : e.care_setting === "outpatient" ? " · outpatient" : ""}
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
                        {fmtDateTime(runOf.get(e.entry_id)!.scheduled_for, { month: "short", day: "numeric", sep: " " })}
                      </span>
                    ) : (
                      <span className="text-ink-muted" title="Not in a scheduled run. Add them to one under Schedules, or a nurse can call them from the queue.">Not in a run</span>
                    )}
                  </td>
                  <td className="px-3 py-3"><StatusPill entry={e} allowed={isAllowed(e.phone, allowlist)} held={e.list_status === "pending" ? holdReason(e, langs) : null} /></td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-1">
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
            <span className="font-semibold text-ink">Authorized numbers.</span>{" "}
            {allowlist.unrestricted
              ? "Any number may be dialled on this deployment."
              : `The agent only dials numbers on the approved list (${allowlist.count}). A patient whose number is not on it is refused at dial time and marked blocked.`}
          </span>
          <button onClick={onManageNumbers} className="ml-auto rounded-full bg-white px-3.5 py-1.5 font-semibold text-brand-deep ring-1 ring-black/[0.08] hover:bg-brand/[0.06]">
            Manage authorized numbers
          </button>
        </Panel>
      ) : null}

      <Panel className="p-5 text-[13px] text-ink-soft">
        <h3 className="font-display text-[14px] font-semibold text-ink">How the planned time is used</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>A call run is a date and time plus the patients to call then. Every scheduled run and its patients appear on the call queue.</li>
          <li>A Cloud Scheduler job checks every minute. When a run’s time arrives it calls the run’s patients who are still pending, once. Anyone not reached gets the usual text follow-up.</li>
          <li>A patient can be in only one scheduled run at a time. Removing the last patient from a run removes the run.</li>
          <li>Auto-recall: a patient a run did not reach, who has not answered the follow-up text, is put in an automatic run after the interval set under Schedules, inside the allowed hours and days. A nurse-scheduled run resets the count.</li>
          <li>A patient who is not in a run is never called automatically. A nurse can call anyone from the call queue at any moment, in the nurse console.</li>
          <li>Only a completed questionnaire (by phone or by text) clears a patient from the list.</li>
        </ul>
      </Panel>

      <EntryDialog
        open={dialog.open}
        entry={dialog.entry}
        actions={actions}
        tz={settings?.timezone ?? "America/New_York"}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
      />
      <ImportDialog
        fileName={imported?.name ?? ""}
        preview={imported?.preview ?? null}
        actions={actions}
        onClose={() => setImported(null)}
      />
    </>
  );
}

const STATUS: Record<CallListEntry["list_status"], { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "info" },
  calling: { label: "Calling now", tone: "good" },
  completed: { label: "Completed", tone: "good" },
  blocked: { label: "Blocked", tone: "warn" },
  canceled: { label: "Cancelled", tone: "warn" },
};

function StatusPill({ entry, allowed, held }: { entry: CallListEntry; allowed?: boolean; held?: string | null }) {
  const s = STATUS[entry.list_status] ?? { label: entry.list_status, tone: "muted" as Tone };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Pill tone={s.tone} pulse={entry.list_status === "calling"}>{s.label}</Pill>
      {allowed === false ? <Pill tone="warn" icon={ShieldAlert}>Not an approved number</Pill> : null}
      {held ? (
        <span title="Not called until the language is available. Edit the patient and tick Call in English to call them in English now.">
          <Pill tone="warn" icon={Languages}>Held: {held.charAt(0).toLowerCase() + held.slice(1)}</Pill>
        </span>
      ) : entry.call_in_english ? <span className="text-[11px] text-ink-muted">calls in English</span> : null}
      {entry.attempts > 0 ? <span className="text-[11px] text-ink-muted">{entry.attempts} {entry.attempts === 1 ? "attempt" : "attempts"}{entry.last_call_status ? ` · ${entry.last_call_status}` : ""}</span> : null}
      {entry.last_error ? <span className="text-[11px] text-brand-coral-dark">{entry.last_error}</span> : null}
    </div>
  );
}
