"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";
import { RecallSettings } from "@/lib/types";
import { AdminActions } from "@/lib/nurse/actions";
import { cn } from "@/lib/utils";
import { DEFAULT_CALLING_HOURS } from "@/lib/nurse/window";
import { Panel } from "./parts";
import { ClinicTimeSelect } from "./clinic-time-input";
import { fmtHHMM, useTimeFormat } from "@/lib/time";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const DEFAULT_RECALL: RecallSettings = DEFAULT_CALLING_HOURS;

/**
 * Auto-recall: when a run does not reach a patient and they do not answer
 * the follow-up text, they are added to a new run after this interval,
 * moved to the next moment inside the allowed hours and days.
 */
export function RecallSettingsPanel({
  settings,
  timezone,
  actions,
}: {
  settings: RecallSettings | null;
  timezone?: string;
  actions: Pick<AdminActions, "live" | "saveRecall">;
}) {
  useTimeFormat(); // re-render the example sentence when the format changes
  const [form, setForm] = useState<RecallSettings>(settings ?? DEFAULT_RECALL);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (settings) setForm(settings); }, [settings]);

  const hours = Math.floor(form.interval_minutes / 60);
  const mins = form.interval_minutes % 60;
  const setInterval = (h: number, m: number) =>
    setForm((f) => ({ ...f, interval_minutes: Math.max(0, h) * 60 + Math.max(0, Math.min(59, m)) }));
  const toggleDay = (d: number) =>
    setForm((f) => ({ ...f, days: f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d].sort() }));

  const problems: string[] = [];
  if (form.interval_minutes < 5) problems.push("The interval must be at least 5 minutes.");
  if (form.window_start >= form.window_end) problems.push("The window must end after it starts.");
  if (!form.days.length) problems.push("Pick at least one day.");
  const dirty = JSON.stringify(form) !== JSON.stringify(settings ?? DEFAULT_RECALL);

  const save = async () => {
    setBusy(true);
    try {
      await actions.saveRecall(form);
      if (actions.live) toast.success("Auto-recall settings saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the settings.");
    } finally {
      setBusy(false);
    }
  };

  const field = "h-9 rounded-xl bg-white px-2.5 text-[13px] tabular-nums text-ink ring-1 ring-inset ring-black/[0.1] focus:outline-none focus:ring-2 focus:ring-brand/40 disabled:opacity-50";

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand-turquoise/20 text-brand-deep">
          <RotateCcw className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <h3 className="font-display text-[15px] font-semibold text-ink">Auto-recall</h3>
          <p className="text-[12px] text-ink-muted">
            A patient a run did not reach, who has not answered the follow-up text, is added to a new run.
          </p>
        </div>
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-[13px] font-semibold text-ink">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
          />
          {form.enabled ? "On" : "Off"}
        </label>
      </div>

      <div className={cn("grid gap-5 px-5 py-4 md:grid-cols-3", !form.enabled && "opacity-50")}>
        <div>
          <div className="text-[12px] font-semibold text-ink-soft">Recall after</div>
          <div className="mt-1.5 flex items-center gap-2 text-[13px] text-ink-soft">
            <input type="number" min={0} max={168} value={hours} disabled={!form.enabled}
              onChange={(e) => setInterval(Number(e.target.value) || 0, mins)} className={cn(field, "w-16")} aria-label="Hours" />
            h
            <input type="number" min={0} max={59} step={5} value={mins} disabled={!form.enabled}
              onChange={(e) => setInterval(hours, Number(e.target.value) || 0)} className={cn(field, "w-16")} aria-label="Minutes" />
            min
          </div>
          <div className="mt-3 text-[12px] font-semibold text-ink-soft">At most</div>
          <div className="mt-1.5 flex items-center gap-2 text-[13px] text-ink-soft">
            <input type="number" min={0} max={10} value={form.max_recalls} disabled={!form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, max_recalls: Math.max(0, Math.min(10, Number(e.target.value) || 0)) }))}
              className={cn(field, "w-16")} aria-label="Maximum recalls" />
            recalls in a row
          </div>
        </div>

        <div>
          <div className="text-[12px] font-semibold text-ink-soft">Between</div>
          <div className="mt-1.5 flex items-center gap-2 text-[13px] text-ink-soft">
            <ClinicTimeSelect value={form.window_start} disabled={!form.enabled} step={30}
              onChange={(v) => setForm((f) => ({ ...f, window_start: v }))} className={field} aria-label="Window start" />
            and
            <ClinicTimeSelect value={form.window_end} disabled={!form.enabled} step={30}
              onChange={(v) => setForm((f) => ({ ...f, window_end: v }))} className={field} aria-label="Window end" />
          </div>
          <p className="mt-1.5 text-[11px] text-ink-muted">
            Clinic time{timezone ? ` (${timezone.replace("_", " ")})` : ""}. These hours and days are also the only times a
            cancelled call can be rescheduled to.
          </p>
        </div>

        <div>
          <div className="text-[12px] font-semibold text-ink-soft">On</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {DAYS.map((label, d) => {
              const on = form.days.includes(d);
              return (
                <button key={label} type="button" disabled={!form.enabled} onClick={() => toggleDay(d)} aria-pressed={on}
                  className={cn("h-9 w-11 rounded-xl text-[12px] font-semibold ring-1 ring-inset transition-colors",
                    on ? "bg-brand-deep text-white ring-brand-deep" : "bg-white text-ink-soft ring-black/[0.1] hover:text-ink")}>
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-black/[0.05] px-5 py-3 text-[12px] text-ink-muted">
        {problems.length ? (
          <span className="text-brand-red-dark">{problems.join(" ")}</span>
        ) : form.enabled ? (
          <span>
            Example: a call missed at {fmtHHMM("16:30")} is retried {describe(form)}.
          </span>
        ) : (
          <span>Missed calls are not retried automatically; the follow-up text still goes out.</span>
        )}
        <button
          onClick={save}
          disabled={!dirty || busy || problems.length > 0}
          className="ml-auto rounded-full bg-brand px-4 py-1.5 text-[13px] font-semibold text-white hover:bg-brand-dark disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </Panel>
  );
}

/** A plain-language example of where a 16:30 miss lands. */
function describe(f: RecallSettings): string {
  const missed = 16 * 60 + 30;
  const start = toMin(f.window_start);
  const end = toMin(f.window_end);
  const target = missed + f.interval_minutes;
  const sameDay = target < 24 * 60 && target >= start && target < end;
  const span = `${Math.floor(f.interval_minutes / 60) ? `${Math.floor(f.interval_minutes / 60)}h ` : ""}${f.interval_minutes % 60 ? `${f.interval_minutes % 60}m` : ""}`.trim();
  return sameDay
    ? `${span} later, at ${fmt(target)}`
    : `at ${fmt(start)} on the next allowed day (${span} later falls outside ${fmt(start)}–${fmt(end)})`;
}

const toMin = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
const fmt = (m: number) => {
  const h = Math.floor(m / 60) % 24;
  const mm = String(m % 60).padStart(2, "0");
  return fmtHHMM(`${String(h).padStart(2, "0")}:${mm}`);
};
