"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Ban,
  CheckCircle2,
  CheckSquare,
  MessageSquareText,
  PhoneCall,
  PhoneMissed,
  PhoneOutgoing,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Square,
  XCircle,
} from "lucide-react";
import { ScheduleItem, ScheduleOrigin, dayHeading } from "@/lib/nurse/model";
import { CallRun } from "@/lib/types";
import { NurseActions } from "@/lib/nurse/actions";
import { isAllowed } from "@/lib/nurse/allowlist";
import { AllowlistResponse } from "@/lib/types";
import { getStatusLabel } from "@/lib/status";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Avatar, ChipOption, EmptyState, FilterChips, Panel, Pill, Tone, clock, countdown, isSameDay } from "./parts";

type Filter = "all" | ScheduleOrigin;

const ORIGIN: Record<ScheduleOrigin, { label: string; tone: Tone; icon: React.ComponentType<{ className?: string }> }> = {
  first_call: { label: "First call", tone: "good", icon: Sparkles },
  retry: { label: "Retry", tone: "muted", icon: RotateCcw },
  text_reschedule: { label: "Rescheduled · text", tone: "info", icon: MessageSquareText },
};

/** Only call-list entries can be dialled from here; text bookings belong to the SMS service. */
/** Only run members can be dialled from here; text bookings belong to the SMS service. */
const dialable = (i: ScheduleItem) =>
  !!i.entryId && i.state !== "calling" && i.state !== "done" && i.state !== "blocked";

export function ScheduleView({
  items,
  runs,
  now,
  actions,
  running,
  allowlist,
  onOpen,
}: {
  items: ScheduleItem[];
  /** For the group headers: each run's label and status. */
  runs: CallRun[];
  now: Date;
  actions: NurseActions;
  /** A run is in progress: dial buttons wait for it. */
  running: boolean;
  /** The proxy's outbound allowlist, to warn before a dial is refused. */
  allowlist?: AllowlistResponse | null;
  onOpen: (item: ScheduleItem) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<{ ids: string[]; names: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [cancelAsk, setCancelAsk] = useState<{ ids: string[]; names: string[] } | null>(null);

  const askCancel = (rows: ScheduleItem[]) => {
    const r = rows.filter((i) => i.entryId);
    if (r.length) setCancelAsk({ ids: r.map((i) => i.entryId!), names: r.map((i) => i.patientName) });
  };

  const doCancel = async (reason: string) => {
    if (!cancelAsk) return;
    setBusy(true);
    try {
      await actions.cancelEntries(cancelAsk.ids, reason);
      if (actions.live) {
        toast.success(cancelAsk.ids.length === 1
          ? `Cancelled ${cancelAsk.names[0]}'s call — it is on Call status`
          : `Cancelled ${cancelAsk.ids.length} calls — they are on Call status`);
      }
      setSelected(new Set());
      setCancelAsk(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not cancel.");
    } finally {
      setBusy(false);
    }
  };

  const match = (i: ScheduleItem) => (filter === "all" ? true : i.origin === filter);

  const options: ChipOption<Filter>[] = [
    { value: "all", label: "All", count: items.length },
    { value: "first_call", label: "First calls", count: items.filter((i) => i.origin === "first_call").length, tone: "good" },
    { value: "retry", label: "Retries", count: items.filter((i) => i.origin === "retry").length, tone: "muted" },
    { value: "text_reschedule", label: "Rescheduled · text", count: items.filter((i) => i.origin === "text_reschedule").length, tone: "info" },
  ];

  const visible = items.filter(match);
  // Selection only ever holds rows that are on screen and dialable, so the
  // "Call N selected" button never acts on something the nurse cannot see.
  const visibleDialable = visible.filter(dialable);
  const selectedVisible = visibleDialable.filter((i) => selected.has(i.id));
  const dueToday = visibleDialable.filter((i) => isSameDay(i.when, now) || i.state === "overdue");

  // One group per call run, in time order; text bookings grouped by day.
  const runById = useMemo(() => new Map(runs.map((r) => [r.run_id, r])), [runs]);
  const groups = useMemo(() => {
    const out = new Map<string, { when: string; run?: CallRun; rows: ScheduleItem[] }>();
    for (const i of visible) {
      const key = i.runId ? `run:${i.runId}` : `text:${dayHeading(i.when, now)}`;
      const g = out.get(key) ?? { when: i.when, run: i.runId ? runById.get(i.runId) : undefined, rows: [] };
      g.rows.push(i);
      if (i.when < g.when) g.when = i.when;
      out.set(key, g);
    }
    return [...out.entries()].sort((a, b) => a[1].when.localeCompare(b[1].when));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, filter, now, runById]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected(selectedVisible.length === visibleDialable.length && visibleDialable.length > 0
      ? new Set()
      : new Set(visibleDialable.map((i) => i.id)));

  const ask = (rows: ScheduleItem[]) => {
    if (!rows.length) return;
    setConfirm({ ids: rows.map((i) => i.entryId!).filter(Boolean), names: rows.map((i) => i.patientName) });
  };

  const place = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      await actions.callEntries(confirm.ids);
      if (actions.live) toast.success(confirm.ids.length === 1 ? `Calling ${confirm.names[0]}` : `Calling ${confirm.ids.length} patients`);
      setSelected(new Set());
      setConfirm(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start the calls.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <FilterChips options={options} value={filter} onChange={setFilter} />

      {/* Dial bar: the nurse decides what gets called now, overriding the plan. */}
      <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
        <button
          onClick={toggleAll}
          disabled={!visibleDialable.length}
          className="inline-flex items-center gap-2 text-[13px] font-semibold text-ink-soft hover:text-ink disabled:opacity-40"
        >
          {selectedVisible.length && selectedVisible.length === visibleDialable.length ? (
            <CheckSquare className="h-4 w-4 text-brand" />
          ) : (
            <Square className="h-4 w-4" />
          )}
          {selectedVisible.length ? `${selectedVisible.length} selected` : "Select all"}
        </button>
        <span className="hidden text-[12px] text-ink-muted sm:inline">
          {running ? "A run is in progress — new calls start after it." : "Calls placed from here ignore the planned time."}
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button
            onClick={() => ask(selectedVisible)}
            disabled={!selectedVisible.length || running || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition-transform hover:-translate-y-px disabled:translate-y-0 disabled:opacity-40"
          >
            <PhoneOutgoing className="h-3.5 w-3.5" /> Call selected{selectedVisible.length ? ` (${selectedVisible.length})` : ""}
          </button>
          <button
            onClick={() => askCancel(selectedVisible)}
            disabled={!selectedVisible.length || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-brand-red-dark ring-1 ring-black/[0.08] transition-colors hover:bg-brand-red/[0.06] disabled:opacity-40"
          >
            <XCircle className="h-3.5 w-3.5" /> Cancel selected{selectedVisible.length ? ` (${selectedVisible.length})` : ""}
          </button>
          <button
            onClick={() => ask(dueToday)}
            disabled={!dueToday.length || running || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-brand-deep ring-1 ring-black/[0.08] transition-colors hover:bg-brand/[0.06] disabled:opacity-40"
          >
            Call everyone due today ({dueToday.length})
          </button>
          <button
            onClick={() => ask(visibleDialable)}
            disabled={!visibleDialable.length || running || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] transition-colors hover:text-ink disabled:opacity-40"
          >
            Call all ({visibleDialable.length})
          </button>
        </div>
      </Panel>

      {groups.length === 0 ? (
        <Panel>
          <EmptyState title="No call runs scheduled" hint="Create a run under Settings: pick a date and time and add patients. Text bookings arrive here on their own." />
        </Panel>
      ) : (
        groups.map(([key, { when, run, rows }]) => (
          <section key={key}>
            <GroupHeader when={when} run={run} count={rows.length} now={now} />
            <Panel className="divide-y divide-black/[0.05] overflow-hidden">
              {rows.map((i) => (
                <ScheduleRow
                  key={i.id}
                  item={i}
                  now={now}
                  allowed={isAllowed(i.phone, allowlist)}
                  selected={selected.has(i.id)}
                  running={running || busy}
                  onToggle={() => toggle(i.id)}
                  onCall={() => ask([i])}
                  onCancel={() => askCancel([i])}
                  onOpen={() => onOpen(i)}
                />
              ))}
            </Panel>
          </section>
        ))
      )}

      {cancelAsk ? (
        <CancelDialog
          names={cancelAsk.names}
          busy={busy}
          live={actions.live}
          onClose={() => setCancelAsk(null)}
          onConfirm={doCancel}
        />
      ) : null}

      {confirm ? (
        <ConfirmDial
          names={confirm.names}
          busy={busy}
          live={actions.live}
          onCancel={() => setConfirm(null)}
          onConfirm={place}
        />
      ) : null}
    </div>
  );
}

function ScheduleRow({
  item,
  now,
  allowed,
  selected,
  running,
  onToggle,
  onCall,
  onCancel,
  onOpen,
}: {
  item: ScheduleItem;
  now: Date;
  /** undefined = allowlist unknown (sample data). */
  allowed?: boolean;
  selected: boolean;
  running: boolean;
  onToggle: () => void;
  onCall: () => void;
  onCancel: () => void;
  onOpen: () => void;
}) {
  const o = ORIGIN[item.origin];
  const due = item.state === "upcoming" || item.state === "overdue" ? countdown(item.when, now) : null;
  const canDial = dialable(item);
  return (
    <div className="group grid w-full grid-cols-[2rem_4.5rem_1fr] items-center gap-3 px-4 py-4 sm:grid-cols-[2rem_5.5rem_1fr_auto] sm:gap-4 sm:px-5">
      <button
        onClick={onToggle}
        disabled={!canDial}
        aria-label={selected ? `Deselect ${item.patientName}` : `Select ${item.patientName}`}
        className="grid h-8 w-8 place-items-center rounded-lg text-ink-muted hover:bg-brand/[0.06] disabled:opacity-25"
      >
        {selected ? <CheckSquare className="h-4.5 w-4.5 text-brand" /> : <Square className="h-4.5 w-4.5" />}
      </button>

      <button onClick={onOpen} className="text-left">
        {item.state === "calling" || item.state === "overdue" || new Date(item.when).getTime() <= now.getTime() ? (
          // The queue never shows a time that has passed.
          <div className={cn("font-display text-lg font-semibold leading-none",
            item.state === "overdue" ? "text-brand-coral-dark" : "text-brand-dark")}>Now</div>
        ) : (
          <>
            <div className="font-display text-lg font-semibold tabular-nums leading-none text-ink">
              {clock(item.when).replace(/\s?(AM|PM)$/i, "")}
              <span className="ml-0.5 text-[11px] font-medium text-ink-muted">{clock(item.when).match(/AM|PM/i)?.[0]}</span>
            </div>
            <div className="mt-1 text-[11px] text-ink-muted">{due?.text}</div>
          </>
        )}
      </button>

      <button onClick={onOpen} className="flex min-w-0 items-center gap-3.5 text-left">
        <Avatar name={item.patientName} tone={o.tone} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-display text-[15px] font-semibold text-ink">{item.patientName}</span>
            <Pill tone={o.tone} icon={o.icon}>{o.label}</Pill>
            {item.origin === "retry" ? <span className="text-[11px] text-ink-muted">attempt {item.attempts + 1}</span> : null}
            {allowed === false ? <Pill tone="warn" icon={ShieldAlert}>Not an approved number</Pill> : null}
          </div>
          <div className="mt-1 truncate text-[13px] text-ink-soft">
            {item.procedure ?? "—"}
            <span className="mx-1.5 text-rule">•</span>
            <span className="tabular-nums">{formatPhone(item.phone)}</span>
            {item.whenText ? <span className="ml-2 italic text-ink-muted">“{item.whenText}”</span> : null}
          </div>
        </div>
      </button>

      <div className="col-span-3 flex items-center justify-end gap-2 sm:col-span-1">
        <StateBadge item={item} />
        {canDial ? (
          <button
            onClick={onCall}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand/10 px-3 py-1.5 text-[12px] font-semibold text-brand-dark transition-colors hover:bg-brand hover:text-white disabled:opacity-40"
          >
            <PhoneCall className="h-3.5 w-3.5" /> Call now
          </button>
        ) : null}
        {canDial ? (
          <button
            onClick={onCancel}
            aria-label={`Cancel ${item.patientName}'s call`}
            title="Cancel this call"
            className="grid h-8 w-8 place-items-center rounded-full text-ink-muted transition-colors hover:bg-brand-red/10 hover:text-brand-red-dark"
          >
            <XCircle className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

function GroupHeader({ when, run, count, now }: { when: string; run?: CallRun; count: number; now: Date }) {
  const past = new Date(when).getTime() <= now.getTime();
  const day = dayHeading(when, now);
  const missed = run?.status === "scheduled" && new Date(when).getTime() + 3 * 60_000 < now.getTime();
  // Never a past time: a group that has started reads "In progress" / "Starting now".
  const title = run
    ? run.status !== "scheduled" ? "In progress" : past ? "Starting now" : `${day} · ${clock(when)}`
    : past ? "Text bookings · now" : `Text bookings · ${day}`;
  return (
    <div className="mb-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 px-1">
      <h3 className={cn("font-display text-sm font-semibold uppercase tracking-[0.12em]",
        missed ? "text-brand-coral-dark" : "text-ink-soft")}>
        {title}
      </h3>
      {run?.label ? <span className="text-[13px] font-semibold text-ink">{run.label}</span> : null}
      <span className="text-xs text-ink-muted">{count} {count === 1 ? "patient" : "patients"}</span>
      {run ? (
        <span className="ml-auto">
          {run.status === "dispatched" ? (
            <Pill tone="good" pulse>Calling</Pill>
          ) : run.status === "dispatching" ? (
            <Pill tone="good" pulse>Starting</Pill>
          ) : missed ? (
            <Pill tone="urgent">Scheduler has not started it</Pill>
          ) : (
            <Pill tone="muted">Scheduled · {countdown(when, now).text}</Pill>
          )}
        </span>
      ) : null}
    </div>
  );
}

function StateBadge({ item }: { item: ScheduleItem }) {
  switch (item.state) {
    case "calling":
      return <Pill tone="good" pulse>Calling now</Pill>;
    case "done":
      return <Pill tone="good" icon={CheckCircle2}>Completed</Pill>;
    case "blocked":
      return <Pill tone="warn" icon={Ban}>Blocked · not an approved number</Pill>;
    case "overdue":
      return <Pill tone="urgent">Not dialled yet</Pill>;
    case "attempted":
      return (
        <Pill tone="teal" icon={PhoneMissed}>
          {item.lastCallStatus ? getStatusLabel(item.lastCallStatus) : "Not reached"} · follow-up by text
        </Pill>
      );
    default:
      return <Pill tone="muted" icon={PhoneCall}>Scheduled</Pill>;
  }
}

/** Every dial is confirmed by name. A real phone rings on the other end. */
/** Cancelling a call needs a reason; it is shown on the Call status board. */
function CancelDialog({
  names,
  busy,
  live,
  onClose,
  onConfirm,
}: {
  names: string[];
  busy: boolean;
  live: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 5;
  const shown = names.slice(0, 6);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-brand-deep-dark/30 p-4 backdrop-blur-[2px]" role="dialog" aria-modal="true">
      <Panel className="w-full max-w-md bg-white p-6">
        <h3 className="font-display text-lg font-semibold text-ink">
          {names.length === 1 ? `Cancel ${names[0]}'s call?` : `Cancel ${names.length} calls?`}
        </h3>
        <p className="mt-1.5 text-[13px] text-ink-soft">
          {live
            ? "They come off the queue and move to Call status as Cancelled. You can reschedule them from there."
            : "Sample data: nothing is changed."}
        </p>
        {names.length > 1 ? (
          <ul className="mt-3 max-h-32 space-y-1 overflow-y-auto rounded-xl bg-mist/60 p-3 text-[13px] text-ink">
            {shown.map((n, i) => <li key={`${n}-${i}`}>{n}</li>)}
            {names.length > shown.length ? <li className="text-ink-muted">…and {names.length - shown.length} more</li> : null}
          </ul>
        ) : null}
        <label className="mt-4 block">
          <span className="text-[12px] font-semibold text-ink-soft">Reason <span className="text-brand-red-dark">*</span></span>
          <textarea
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            maxLength={500}
            placeholder="e.g. Patient readmitted; surgeon asked to hold follow-up calls."
            className="mt-1 w-full rounded-xl bg-mist/60 px-3 py-2 text-[13px] text-ink ring-1 ring-inset ring-black/[0.06] placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-brand/40"
          />
          {!ok && reason.length > 0 ? <span className="text-[11px] text-brand-red-dark">Say why, in a few words at least.</span> : null}
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} disabled={busy} className="rounded-full px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
            Keep the call
          </button>
          <button
            onClick={() => onConfirm(reason.trim())}
            disabled={!ok || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand-red px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-red-dark disabled:opacity-40"
          >
            <XCircle className="h-3.5 w-3.5" /> {busy ? "Cancelling…" : names.length === 1 ? "Cancel call" : `Cancel ${names.length} calls`}
          </button>
        </div>
      </Panel>
    </div>
  );
}

function ConfirmDial({
  names,
  busy,
  live,
  onCancel,
  onConfirm,
}: {
  names: string[];
  busy: boolean;
  live: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const shown = names.slice(0, 6);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-brand-deep-dark/30 p-4 backdrop-blur-[2px]" role="dialog" aria-modal="true">
      <Panel className="w-full max-w-md bg-white p-6">
        <h3 className="font-display text-lg font-semibold text-ink">
          {names.length === 1 ? `Call ${names[0]} now?` : `Call ${names.length} patients now?`}
        </h3>
        <p className="mt-1.5 text-[13px] text-ink-soft">
          {live
            ? "This places real calls through the automated agent, ignoring any planned time."
            : "Sample data: nothing is dialled. On live data this places real calls."}
        </p>
        {names.length > 1 ? (
          <ul className="mt-3 max-h-40 space-y-1 overflow-y-auto rounded-xl bg-mist/60 p-3 text-[13px] text-ink">
            {shown.map((n, i) => <li key={`${n}-${i}`}>{n}</li>)}
            {names.length > shown.length ? <li className="text-ink-muted">…and {names.length - shown.length} more</li> : null}
          </ul>
        ) : null}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onCancel} disabled={busy} className="rounded-full px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
            <PhoneOutgoing className="h-3.5 w-3.5" /> {busy ? "Starting…" : "Place calls"}
          </button>
        </div>
      </Panel>
    </div>
  );
}
