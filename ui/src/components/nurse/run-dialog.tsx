"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Search } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isoToLocalInput, localInputToIso } from "@/components/patient-fields";
import { CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { inCallingWindow, nextCallingSlot, windowText } from "@/lib/nurse/window";
import { DEFAULT_RECALL } from "./recall-settings";
import { NurseActions } from "@/lib/nurse/actions";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { clock } from "./parts";

/**
 * Create or edit a call run: a date/time and the patients to call then.
 *
 * Rules mirrored from the backend so the nurse sees them before saving:
 * at least one patient to create; a time in the future; a patient already in
 * another scheduled run cannot be added; unchecking every patient of an
 * existing run removes the run.
 */
export function RunDialog({
  open,
  run,
  runs,
  entries,
  actions,
  settings,
  onOpenChange,
}: {
  open: boolean;
  /** null = new run. */
  run: CallRun | null;
  runs: CallRun[];
  entries: CallListEntry[];
  actions: NurseActions;
  /** Clinic calling hours (the Auto-recall window). The proxy enforces them too. */
  settings?: CallSettingsResponse | null;
  onOpenChange: (open: boolean) => void;
}) {
  const hours = settings?.recall ?? DEFAULT_RECALL;
  const tz = settings?.timezone ?? "America/New_York";
  const [when, setWhen] = useState("");
  const [label, setLabel] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setWhen(run ? isoToLocalInput(run.scheduled_for) : defaultTime(hours, tz));
    setLabel(run?.label ?? "");
    setPicked(new Set(run?.entry_ids ?? []));
    setQuery("");
    setError(null);
  }, [open, run]);

  // Which patient is booked in which OTHER scheduled run.
  const bookedIn = useMemo(() => {
    const m = new Map<string, CallRun>();
    for (const r of runs) {
      if (r.status !== "scheduled" || r.run_id === run?.run_id) continue;
      for (const id of r.entry_ids) m.set(id, r);
    }
    return m;
  }, [runs, run]);

  const q = query.trim().toLowerCase();
  const candidates = entries
    .filter((e) => e.list_status === "pending" || picked.has(e.entry_id))
    .filter((e) => !q || e.patient_name.toLowerCase().includes(q) || e.patient_id.toLowerCase().includes(q))
    .sort((a, b) => a.patient_name.localeCompare(b.patient_name));

  const iso = localInputToIso(when);
  const inPast = iso !== null && new Date(iso).getTime() < Date.now() - 60_000;
  const removing = !!run && picked.size === 0;
  const outOfHours = iso !== null && !inCallingWindow(iso, hours, tz);
  // Same rule as the proxy: a new run, a moved run, or an added patient must
  // land inside calling hours. Only removing patients is allowed as-is.
  // Compared at the picker's precision (minutes), so an untouched time is unchanged.
  const timeChanged = !run || iso !== localInputToIso(isoToLocalInput(run.scheduled_for));
  const adding = !run || [...picked].some((id) => !run.entry_ids.includes(id));
  const blockedByHours = outOfHours && (timeChanged || adding);
  const canSave = !busy && (removing || (picked.size > 0 && iso !== null && !inPast && !blockedByHours));

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = async () => {
    setError(null);
    if (removing && !window.confirm("No patients are left in this run, so saving removes the run. Continue?")) return;
    setBusy(true);
    try {
      const ids = entries.filter((e) => picked.has(e.entry_id)).map((e) => e.entry_id);
      if (run) {
        await actions.updateRun(run, removing
          ? { entry_ids: [] }
          : { ...(timeChanged ? { scheduled_for: iso! } : {}), entry_ids: ids, label: label.trim() || null });
        if (actions.live) toast.success(removing ? "Run removed" : "Run updated");
      } else {
        await actions.createRun({ scheduled_for: iso!, entry_ids: ids, label: label.trim() || null });
        if (actions.live) toast.success(`Run scheduled for ${new Date(iso!).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`);
      }
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the run.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">{run ? "Edit call run" : "New call run"}</DialogTitle>
          <DialogDescription>
            At the chosen time the agent calls every patient in the run who is still pending. A patient
            already reached is skipped.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="run-when">Date and time *</Label>
              <Input id="run-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
              {iso && Intl.DateTimeFormat().resolvedOptions().timeZone !== tz ? (
                <p className="text-xs text-ink-muted">
                  = {new Date(iso).toLocaleString(undefined, { timeZone: tz, weekday: "short", hour: "numeric", minute: "2-digit" })} clinic time
                </p>
              ) : null}
              {inPast ? (
                <p className="text-xs text-brand-red-dark">That time has passed.</p>
              ) : blockedByHours ? (
                <p className="text-xs text-brand-red-dark">
                  Outside calling hours ({windowText(hours)}).{" "}
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => { const n = nextCallingSlot(new Date(iso!).getTime(), hours, tz); if (n) setWhen(isoToLocalInput(n)); }}
                  >
                    Use the next allowed time
                  </button>
                </p>
              ) : outOfHours ? (
                <p className="text-xs text-ink-soft">This run is outside calling hours; patients can be removed but not added.</p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="run-label">Name <span className="font-normal text-ink-soft">(optional)</span></Label>
              <Input id="run-label" placeholder="e.g. Post-op day 2" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} />
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Patients * <span className="font-normal text-ink-soft">({picked.size} selected)</span></Label>
              <label className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find"
                  className="h-8 w-36 rounded-full bg-mist/60 pl-8 pr-3 text-[13px] ring-1 ring-inset ring-black/[0.06] focus:outline-none focus:ring-2 focus:ring-brand/40"
                />
              </label>
            </div>
            <div className="max-h-64 divide-y divide-black/[0.05] overflow-y-auto rounded-2xl ring-1 ring-inset ring-black/[0.08]">
              {candidates.length === 0 ? (
                <p className="p-4 text-[13px] text-ink-muted">
                  {entries.length ? "No pending patients match." : "Add patients to the list first."}
                </p>
              ) : (
                candidates.map((e) => {
                  const other = bookedIn.get(e.entry_id);
                  const checked = picked.has(e.entry_id);
                  return (
                    <label
                      key={e.entry_id}
                      className={cn("flex items-center gap-3 px-3.5 py-2.5 text-[13px]",
                        other && !checked ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-brand/[0.04]")}
                    >
                      <input
                        type="checkbox"
                        className="accent-brand"
                        checked={checked}
                        disabled={!!other && !checked}
                        onChange={() => toggle(e.entry_id)}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block font-semibold text-ink">{e.patient_name}</span>
                        <span className="block text-[12px] text-ink-muted">
                          {e.patient_id} · {formatPhone(e.phone)}{e.operation_name ? ` · ${e.operation_name}` : ""}
                        </span>
                      </span>
                      {other ? (
                        <span className="shrink-0 text-[11px] text-ink-muted">
                          in run {new Date(other.scheduled_for).toLocaleDateString(undefined, { month: "short", day: "numeric" })} {clock(other.scheduled_for)}
                        </span>
                      ) : null}
                    </label>
                  );
                })
              )}
            </div>
            {!run && picked.size === 0 ? (
              <p className="text-xs text-ink-soft">A run needs at least one patient.</p>
            ) : null}
            {removing ? (
              <p className="text-xs text-brand-red-dark">No patients left: saving removes this run.</p>
            ) : null}
          </div>

          {error ? <p className="rounded-xl bg-brand-red/10 p-2 text-sm text-brand-red-dark">{error}</p> : null}
        </div>

        <p className="text-[11px] text-ink-muted">Calling hours: {windowText(hours)} clinic time (set under Auto-recall).</p>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" onClick={save} disabled={!canSave} variant={removing ? "destructive" : "default"}>
            {busy ? "Saving…" : removing ? "Remove run" : run ? "Save run" : "Schedule run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The next allowed quarter hour, at least 15 minutes out. */
function defaultTime(hours: import("@/lib/types").RecallSettings, tz: string): string {
  const slot = nextCallingSlot(Date.now() + 15 * 60_000, hours, tz);
  return isoToLocalInput(slot ?? new Date(Date.now() + 15 * 60_000).toISOString());
}
