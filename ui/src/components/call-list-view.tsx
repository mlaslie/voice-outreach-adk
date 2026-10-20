"use client";

import { useMemo, useState } from "react";
import {
  Loader2,
  Play,
  Square,
  RotateCw,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCallList } from "@/hooks/use-call-list";
import { api, ApiError } from "@/lib/api";
import { CallListEntry } from "@/lib/types";
import { formatPhone, relativeTime } from "@/lib/format";
import { getStatusLabel } from "@/lib/status";
import { MovedToAdmin } from "./moved-to-admin";

const LIST_STATUS_CLASSES: Record<string, string> = {
  pending: "bg-mist text-ink",
  calling: "bg-brand-turquoise/25 text-brand-deep",
  completed: "bg-brand/15 text-brand-dark",
  blocked: "bg-brand-red/15 text-brand-red-dark",
};

const LIST_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  calling: "Calling…",
  completed: "Completed",
  blocked: "Blocked",
};

/**
 * Classic call list. Patient records, CSV import, reset and the allowed
 * numbers are configuration and moved to the admin console (the API refuses
 * them from this build); running, stopping and re-dialling calls stay.
 */
export function CallListView() {
  const { entries, loading, error, run, refreshEntries, refreshRun } = useCallList();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const isRunning = run?.status === "running";
  // Both "completed" and "blocked" are terminal — neither is dialed again.
  const pending = useMemo(
    () => entries.filter(
      (e) => e.list_status !== "completed" && e.list_status !== "blocked"
    ),
    [entries]
  );
  const blockedCount = useMemo(
    () => entries.filter((e) => e.list_status === "blocked").length,
    [entries]
  );

  const allSelected = entries.length > 0 && entries.every((e) => selected.has(e.entry_id));
  const someSelected = selected.size > 0 && !allSelected;

  function toggleAll(checked: boolean) {
    setSelected(checked ? new Set(entries.map((e) => e.entry_id)) : new Set());
  }

  function toggleOne(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  // Re-dial a finished patient without re-typing their details. Naming the
  // entry explicitly overrides the terminal-status skip server-side.
  async function handleCallAgain(entry: CallListEntry) {
    setBusy(true);
    try {
      await api.startCallListRun([entry.entry_id]);
      toast.success(`Calling ${entry.patient_name} again`);
      await Promise.all([refreshRun(), refreshEntries()]);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not start the call");
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    const ids = selected.size > 0 ? Array.from(selected) : undefined;
    setBusy(true);
    try {
      const res = await api.startCallListRun(ids);
      toast.success(`Run started — ${res.run?.total ?? 0} patient(s) queued`);
      await Promise.all([refreshRun(), refreshEntries()]);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    setBusy(true);
    try {
      await api.cancelCallListRun();
      toast.success("Run stopping — calls already dialing will finish.");
      await refreshRun();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not stop the run");
    } finally {
      setBusy(false);
    }
  }

  const startLabel =
    selected.size > 0
      ? `Call ${selected.size} selected`
      : `Call ${pending.length} pending`;

  return (
    <div className="space-y-4">
      {/* Run status */}
      {run && (
        <div
          className={`rounded-lg border px-4 py-3 ${
            isRunning
              ? "border-brand-turquoise bg-brand-turquoise/10"
              : run.status === "error"
              ? "border-brand-red/50 bg-brand-red/10"
              : "border-rule bg-mist"
          }`}
        >
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-2 text-sm">
              {isRunning ? (
                <Loader2 className="h-4 w-4 animate-spin text-brand-deep" />
              ) : null}
              <span className="font-medium">
                {isRunning
                  ? run.cancel_requested
                    ? "Run stopping…"
                    : "Run in progress"
                  : run.status === "budget_exhausted"
                  ? "Run stopped — dial budget exhausted"
                  : `Run ${run.status}`}
              </span>
              <span className="text-ink-soft">
                {run.dispatched} of {run.total} dialed · {run.succeeded} completed ·{" "}
                {run.failed} needs retry
              </span>
            </div>
            {isRunning && !run.cancel_requested && (
              <Button variant="outline" size="sm" onClick={handleCancel} disabled={busy}>
                <Square className="h-3.5 w-3.5 mr-1.5" />
                Stop
              </Button>
            )}
          </div>
          {run.in_flight.length > 0 && (
            <p className="text-xs text-brand-deep mt-1.5">
              On the phone: {run.in_flight.map((f) => f.patient_name || f.call_sid).join(", ")}
            </p>
          )}
          {run.errors.length > 0 && (
            <p className="text-xs text-brand-red-dark mt-1.5">
              {run.errors.length} dispatch error{run.errors.length === 1 ? "" : "s"}:{" "}
              {run.errors[0].error}
              {run.errors.length > 1 && " …"}
            </p>
          )}
        </div>
      )}

      <div className="rounded-lg border bg-white">
        {/* Toolbar */}
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              onClick={handleStart}
              // An explicit selection can include already-completed rows —
              // the runner re-dials entries named in entry_ids regardless of
              // their terminal status. Only fall back to the pending count
              // when nothing is selected.
              disabled={busy || isRunning || (selected.size === 0 && pending.length === 0)}
            >
              <Play className="h-4 w-4 mr-1.5" />
              {startLabel}
            </Button>
            <MovedToAdmin what="Adding, importing, editing and removing patients, and the allowed numbers," />
          </div>

          <div className="flex items-center gap-3">
            <span className="text-sm text-ink-soft">
              {pending.length} pending
              {blockedCount > 0 && (
                <span className="text-brand-red-dark"> · {blockedCount} blocked</span>
              )}{" "}
              · {entries.length} total
            </span>
          </div>
        </div>

        {error && (
          <p className="px-4 py-3 text-sm text-brand-red-dark bg-brand-red/10 border-b">{error}</p>
        )}

        {loading ? (
          <div className="p-4 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex gap-4">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-20" />
              </div>
            ))}
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center px-4">
            <Users className="h-12 w-12 text-ink-muted mb-4" />
            <p className="text-ink font-medium">No patients on the call list</p>
            <p className="text-sm text-ink-soft mt-1 max-w-md">
              Patients are added in the admin console.
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={allSelected ? true : someSelected ? "indeterminate" : false}
                    onCheckedChange={(v) => toggleAll(v === true)}
                    aria-label="Select all"
                  />
                </TableHead>
                <TableHead>Patient</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>DOB</TableHead>
                <TableHead>Operation</TableHead>
                <TableHead>Diabetic</TableHead>
                <TableHead>List status</TableHead>
                <TableHead className="text-right">Attempts</TableHead>
                <TableHead>Last result</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow key={entry.entry_id}>
                  <TableCell className="w-10">
                    <Checkbox
                      checked={selected.has(entry.entry_id)}
                      onCheckedChange={(v) => toggleOne(entry.entry_id, v === true)}
                      aria-label={`Select ${entry.patient_name}`}
                    />
                  </TableCell>
                  <TableCell className="font-medium">
                    {entry.patient_name}
                    <span className="block text-xs font-normal text-ink-soft">
                      {entry.patient_id}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-sm text-ink-soft">
                    {formatPhone(entry.phone)}
                  </TableCell>
                  <TableCell className="text-sm text-ink-soft">{entry.patient_dob}</TableCell>
                  <TableCell className="text-sm text-ink-soft">
                    {entry.operation_name || "—"}
                    {entry.operation_date && (
                      <span className="block text-xs text-ink-muted">
                        {entry.operation_date}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-sm text-ink-soft">
                    {entry.is_diabetic ? "Yes" : "No"}
                  </TableCell>
                  <TableCell>
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        LIST_STATUS_CLASSES[entry.list_status] ?? "bg-mist text-ink"
                      }`}
                    >
                      {LIST_STATUS_LABELS[entry.list_status] ?? entry.list_status}
                    </span>
                  </TableCell>
                  <TableCell className="text-right text-sm text-ink-soft">
                    {entry.attempts ?? 0}
                  </TableCell>
                  <TableCell className="text-sm text-ink-soft">
                    {entry.last_error ? (
                      <span className="text-brand-red-dark" title={entry.last_error}>
                        {entry.list_status === "blocked"
                          ? "Blocked — not dialable"
                          : "Dispatch failed"}
                      </span>
                    ) : entry.last_call_status ? (
                      <>
                        {entry.last_call_status === "timeout"
                          ? "Timed out"
                          : getStatusLabel(entry.last_call_status)}
                        {entry.last_attempt_at && (
                          <span className="block text-xs text-ink-muted">
                            {relativeTime(entry.last_attempt_at)}
                          </span>
                        )}
                      </>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="w-10">
                    <div className="flex items-center justify-end">
                      {entry.list_status !== "calling" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleCallAgain(entry)}
                          disabled={busy || isRunning}
                          title={`Call ${entry.patient_name} now with these details`}
                          className="text-brand-dark hover:bg-brand/10"
                        >
                          <RotateCw className="h-3.5 w-3.5" />
                          <span className="sr-only">Call again</span>
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <p className="text-xs text-ink-soft px-1">
        Patients stay on the list until a call completes. Ring-no-answer, voicemail, busy
        and failed calls are logged in the call history but remain pending for the next run.
        {blockedCount > 0 && (
          <>
            {" "}
            <span className="text-brand-red-dark">
              Blocked entries could not be dialed at all — usually because the number is
              not on the outbound allowlist. Allowing it and resetting the entry are done
              in the admin console.
            </span>
          </>
        )}
      </p>

    </div>
  );
}
