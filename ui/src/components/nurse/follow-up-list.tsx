"use client";

import { useState } from "react";
import { AlertTriangle, CalendarClock, Check, Clock, Phone, Siren, Undo2 } from "lucide-react";
import { FollowUpFilter, FollowUpItem, FollowUpTier, matchesFollowUp } from "@/lib/nurse/model";
import { findingsFor } from "@/lib/triage";
import { QuestionCountPill } from "@/components/patient-questions";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Avatar, ChipOption, EmptyState, FilterChips, Panel, Pill, TONE, Tone, ago, clock, countdown, isSameDay } from "./parts";

export const TIER: Record<FollowUpTier, { label: string; short: string; tone: Tone }> = {
  emergency: { label: "Emergency", short: "Emergency", tone: "emergency" },
  urgent_same_day: { label: "Urgent · same day", short: "Same day", tone: "urgent" },
  routine_next_business_day: { label: "Routine · next business day", short: "Next day", tone: "routine" },
};

export function FollowUpList({
  items,
  now,
  contacted,
  onToggleContacted,
  onOpen,
}: {
  items: FollowUpItem[];
  now: Date;
  contacted: Set<string>;
  onToggleContacted: (id: string) => void;
  onOpen: (item: FollowUpItem) => void;
}) {
  const [filter, setFilter] = useState<FollowUpFilter>("all");
  const [showDone, setShowDone] = useState(false);

  const open = items.filter((i) => !contacted.has(i.id));
  const count = (f: FollowUpFilter) => open.filter((i) => matchesFollowUp(i, f, now)).length;
  const options: ChipOption<FollowUpFilter>[] = [
    { value: "all", label: "All open", count: open.length },
    { value: "emergency", label: "Emergency", count: count("emergency"), tone: "emergency" },
    { value: "urgent_same_day", label: "Urgent · same day", count: count("urgent_same_day"), tone: "urgent" },
    { value: "routine_next_business_day", label: "Next business day", count: count("routine_next_business_day"), tone: "routine" },
    { value: "due_today", label: "Due today", count: count("due_today"), tone: "info" },
    { value: "overdue", label: "Overdue", count: count("overdue"), tone: "urgent" },
  ];

  const rows = items
    .filter((i) => matchesFollowUp(i, filter, now))
    .filter((i) => showDone || !contacted.has(i.id));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <FilterChips options={options} value={filter} onChange={setFilter} />
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink-soft">
          <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} className="accent-brand" />
          Show contacted ({contacted.size})
        </label>
      </div>

      {rows.length === 0 ? (
        <Panel>
          <EmptyState title="All caught up" hint="No patients in this view need a callback." />
        </Panel>
      ) : (
        <div className="grid gap-3.5">
          {rows.map((i) => (
            <FollowUpCard
              key={i.id}
              item={i}
              now={now}
              done={contacted.has(i.id)}
              onToggle={() => onToggleContacted(i.id)}
              onOpen={() => onOpen(i)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FollowUpCard({
  item,
  now,
  done,
  onToggle,
  onOpen,
}: {
  item: FollowUpItem;
  now: Date;
  done: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const t = TIER[item.tier];
  const due = countdown(item.dueBy, now);
  // Questions get their own pill (below), not a flag chip.
  const flags = findingsFor(item.call).filter((f) => (f.level === "alert" || f.level === "watch") && f.key !== "questions");
  const emergency = item.tier === "emergency";

  return (
    <Panel className={cn("relative overflow-hidden transition-all", done && "opacity-55 saturate-50")}>
      <span className={cn("absolute inset-y-0 left-0 w-1.5", TONE[t.tone].stripe)} />
      {emergency && !done ? (
        <span className="pointer-events-none absolute inset-0 bg-gradient-to-r from-brand-red/[0.06] via-transparent to-transparent" />
      ) : null}
      <div className="relative grid gap-4 py-4 pl-6 pr-5 md:grid-cols-[1fr_auto]">
        <button onClick={onOpen} className="flex min-w-0 items-start gap-3.5 text-left">
          <Avatar name={item.patientName} tone={t.tone} />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display text-[15px] font-semibold text-ink">{item.patientName}</span>
              <Pill tone={t.tone} icon={emergency ? Siren : undefined} pulse={emergency && !done}>{t.label}</Pill>
              {item.callback ? (
                <Pill tone="info" icon={CalendarClock}>Callback {isSameDay(item.callback.run_at, now) ? "today" : new Date(item.callback.run_at).toLocaleDateString(undefined, { weekday: "short" })} {clock(item.callback.run_at)}</Pill>
              ) : null}
              <QuestionCountPill call={item.call} />
              {done ? <Pill tone="good" icon={Check}>Contacted</Pill> : null}
            </div>
            <p className="mt-1.5 text-[14px] leading-snug text-ink">{item.reason ?? "No reason recorded."}</p>
            {flags.length ? (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {flags.map((f) => (
                  <span
                    key={f.key}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium",
                      f.level === "alert" ? "bg-brand-red/[0.08] text-brand-red-dark" : "bg-brand-yellow/20 text-ink",
                    )}
                  >
                    {f.level === "alert" ? <AlertTriangle className="h-3 w-3" /> : null}
                    {f.label}: {f.value}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="mt-2.5 text-[12px] text-ink-muted">
              {item.procedure ?? "—"} <span className="mx-1 text-rule">•</span> raised {ago(item.raisedAt, now)}
              <span className="mx-1 text-rule">•</span> <span className="tabular-nums">{formatPhone(item.phone)}</span>
            </div>
          </div>
        </button>

        <div className="flex items-center gap-3 md:flex-col md:items-end md:justify-between">
          <div className={cn("flex items-center gap-1.5 text-[13px] font-semibold tabular-nums",
            emergency ? "text-brand-red-dark" : due.late ? "text-brand-coral-dark" : "text-ink-soft")}>
            <Clock className="h-3.5 w-3.5" />
            {emergency ? "Now" : due.late ? due.text : `Due ${isSameDay(item.dueBy, now) ? "today" : new Date(item.dueBy).toLocaleDateString(undefined, { weekday: "short" })} ${clock(item.dueBy)}`}
          </div>
          <div className="flex gap-2">
            <a
              href={`tel:${item.phone}`}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-3.5 py-2 text-[13px] font-semibold text-white shadow-sm transition-transform hover:-translate-y-px",
                emergency ? "bg-brand-red hover:bg-brand-red-dark" : "bg-brand hover:bg-brand-dark",
              )}
            >
              <Phone className="h-3.5 w-3.5" /> Call
            </a>
            <button
              onClick={onToggle}
              className="inline-flex items-center gap-1.5 rounded-full bg-white px-3.5 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] transition-colors hover:text-ink"
            >
              {done ? <><Undo2 className="h-3.5 w-3.5" /> Reopen</> : <><Check className="h-3.5 w-3.5" /> Contacted</>}
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}
