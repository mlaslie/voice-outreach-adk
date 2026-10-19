"use client";

import { useEffect, useState } from "react";
import {
  Ban,
  CalendarClock,
  CheckCircle2,
  MessageCircleReply,
  MessageSquareText,
  PenLine,
  PhoneMissed,
  PhoneOff,
  ScissorsLineDashed,
  Send,
  UserX,
  Voicemail,
  XCircle,
} from "lucide-react";
import { CompletedFilter, CompletedItem, CompletedKind, DayScope, inScope, matchesCompleted } from "@/lib/nurse/model";
import { formatDuration, formatPhone } from "@/lib/format";
import { getFollowUpLabel } from "@/lib/status";
import { cn } from "@/lib/utils";
import { QuestionCountPill } from "@/components/patient-questions";
import { Avatar, ChipOption, EmptyState, FilterChips, Panel, Pill, Tone, ago, clock, isSameDay } from "./parts";
import { ReasonSummaryLine } from "@/components/follow-up-reasons";
import { reasonsFor } from "@/lib/follow-up-reasons";

export const KIND: Record<CompletedKind, { label: string; tone: Tone; icon: React.ComponentType<{ className?: string }> }> = {
  completed_phone: { label: "Completed · phone", tone: "good", icon: CheckCircle2 },
  completed_text: { label: "Completed · text", tone: "info", icon: MessageSquareText },
  voicemail: { label: "Left voicemail", tone: "teal", icon: Voicemail },
  no_answer: { label: "No answer", tone: "muted", icon: PhoneMissed },
  unreachable: { label: "Busy / failed", tone: "warn", icon: PhoneOff },
  interrupted: { label: "Dropped", tone: "urgent", icon: ScissorsLineDashed },
  reschedule: { label: "Asked to reschedule", tone: "teal", icon: CalendarClock },
  wrong_person: { label: "Wrong person", tone: "warn", icon: UserX },
  declined: { label: "Declined", tone: "muted", icon: Ban },
  canceled: { label: "Cancelled", tone: "warn", icon: XCircle },
};

const TIER_TONE: Record<string, Tone> = {
  emergency: "emergency",
  urgent_same_day: "urgent",
  routine_next_business_day: "routine",
  informational: "info",
  none: "good",
};

const SCOPES: { value: DayScope; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "Last 7 days" },
  { value: "all", label: "Everything" },
];

/** Open Call status at a given filter and range (from a KPI tile or the
 *  emergency banner). `n` changes on every request so a repeat applies. */
export type CompletedPreset = { filter: CompletedFilter; scope: DayScope; n: number };

export function CompletedView({
  items,
  now,
  onOpen,
  overdueCallIds = new Set<string>(),
  preset,
}: {
  items: CompletedItem[];
  now: Date;
  onOpen: (item: CompletedItem) => void;
  /** Calls whose open follow-up is past its callback window. */
  overdueCallIds?: Set<string>;
  preset?: CompletedPreset | null;
}) {
  const [scope, setScope] = useState<DayScope>(preset?.scope ?? "today");
  const [filter, setFilter] = useState<CompletedFilter>(preset?.filter ?? "all");
  useEffect(() => {
    if (preset) { setScope(preset.scope); setFilter(preset.filter); }
  }, [preset]);

  const matches = (i: CompletedItem, f: CompletedFilter) =>
    f === "overdue" ? !!i.call && overdueCallIds.has(i.call.doc_id) : matchesCompleted(i, f);
  const inRange = items.filter((i) => inScope(i, scope, now));
  const count = (f: CompletedFilter) => inRange.filter((i) => matches(i, f)).length;

  // Two rows of chips: what the nurse must DO, then what HAPPENED.
  const action: ChipOption<CompletedFilter>[] = [
    { value: "all", label: "All", count: inRange.length },
    { value: "needs_immediate", label: "Immediate follow-up", count: count("needs_immediate"), tone: "emergency" },
    { value: "next_business_day", label: "Next business day", count: count("next_business_day"), tone: "routine" },
    { value: "no_follow_up", label: "No follow-up needed", count: count("no_follow_up"), tone: "good" },
    ...(count("overdue") ? [{ value: "overdue" as CompletedFilter, label: "Overdue", count: count("overdue"), tone: "urgent" as const }] : []),
  ];
  const outcome: ChipOption<CompletedFilter>[] = [
    { value: "completed_phone", label: "Completed · phone", count: count("completed_phone"), tone: "good" },
    { value: "completed_text", label: "Completed · text", count: count("completed_text"), tone: "info" },
    { value: "voicemail", label: "Left voicemail", count: count("voicemail"), tone: "teal" },
    { value: "text_sent", label: "Text sent", count: count("text_sent"), tone: "info" },
    { value: "callback_booked", label: "Callback booked", count: count("callback_booked"), tone: "teal" },
    { value: "reschedule", label: "Asked to reschedule", count: count("reschedule"), tone: "teal" },
    { value: "no_answer", label: "No answer", count: count("no_answer"), tone: "muted" },
    { value: "unreachable", label: "Unreachable", count: count("unreachable"), tone: "warn" },
    { value: "interrupted", label: "Dropped", count: count("interrupted"), tone: "urgent" },
    { value: "canceled", label: "Cancelled", count: count("canceled"), tone: "warn" },
  ];

  const rows = inRange.filter((i) => matches(i, filter));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-full bg-white/70 p-1 ring-1 ring-black/[0.06]" role="tablist" aria-label="Time range">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              role="tab"
              aria-selected={scope === s.value}
              onClick={() => setScope(s.value)}
              className={cn("rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                scope === s.value ? "bg-brand-deep text-white" : "text-ink-soft hover:text-ink")}
            >
              {s.label}
            </button>
          ))}
        </div>
        <span className="text-[12px] text-ink-muted">
          {inRange.length} {inRange.length === 1 ? "call" : "calls"}
          {scope === "today" ? ` · ${now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}` : ""}
        </span>
      </div>
      <FilterChips options={action} value={filter} onChange={setFilter} />
      <FilterChips options={outcome} value={filter} onChange={setFilter} />

      <Panel className="overflow-hidden">
        {rows.length === 0 ? (
          <EmptyState
            title={inRange.length ? "No calls match this filter" : scope === "today" ? "No calls finished today yet" : "No calls in this range"}
            hint={inRange.length ? undefined : "Calls appear here the moment they end, including text conversations."}
          />
        ) : (
          <ul className="divide-y divide-black/[0.05]">
            {rows.map((i) => {
              const k = KIND[i.kind];
              const tierTone = i.followUp ? TIER_TONE[i.followUp] : undefined;
              return (
                <li key={i.id}>
                  <button
                    onClick={() => onOpen(i)}
                    className="grid w-full grid-cols-[1fr_auto] items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-brand/[0.035]"
                  >
                    <div className="flex min-w-0 items-center gap-3.5">
                      <Avatar name={i.patientName} tone={tierTone ?? k.tone} />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate font-display text-[15px] font-semibold text-ink">{i.patientName}</span>
                          <Pill tone={k.tone} icon={k.icon}>{k.label}</Pill>
                          {i.textSent ? <Pill tone="info" icon={Send}>Text sent</Pill> : null}
                          {i.textSkipped ? (
                            <span title={`Follow-up text not sent: ${i.textSkipped}`}>
                              <Pill tone="warn" icon={Send}>Text not sent</Pill>
                            </span>
                          ) : null}
                          {i.textReplied ? <Pill tone="good" icon={MessageCircleReply}>Replied</Pill> : null}
                          {i.callback ? (
                            <Pill tone="teal" icon={CalendarClock}>
                              Callback {isSameDay(i.callback.run_at, now) ? "today" : new Date(i.callback.run_at).toLocaleDateString(undefined, { weekday: "short" })} {clock(i.callback.run_at)}
                            </Pill>
                          ) : null}
                          {i.optedOut ? <Pill tone="emergency">Opted out</Pill> : null}
                          {i.call ? <QuestionCountPill call={i.call} /> : null}
                        </div>
                        <div className="mt-1 truncate text-[13px] text-ink-soft">
                          {i.procedure ?? "—"}
                          <span className="mx-1.5 text-rule">•</span>
                          <span className="tabular-nums">{formatPhone(i.phone)}</span>
                          {i.cancel ? (
                            <>
                              <span className="mx-1.5 text-rule">•</span>
                              <span className="text-ink">Cancelled{i.cancel.by ? ` by ${i.cancel.by}` : ""}: {i.cancel.reason}</span>
                            </>
                          ) : reasonsFor(i.call).length ? (
                            <>
                              <span className="mx-1.5 text-rule">•</span>
                              <ReasonSummaryLine call={i.call} className="max-w-full align-middle text-ink" />
                            </>
                          ) : null}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1.5 text-right">
                      {i.kind === "canceled" ? (
                        <Pill tone="warn">Not called</Pill>
                      ) : i.followUp && tierTone ? (
                        <Pill tone={tierTone} icon={i.overridden ? PenLine : undefined}>
                          {getFollowUpLabel(i.followUp)}{i.overridden ? " · nurse" : ""}
                        </Pill>
                      ) : (
                        <Pill tone="muted">Not assessed</Pill>
                      )}
                      <div className="text-[11px] text-ink-muted">
                        <span className="font-display text-[13px] font-semibold tabular-nums text-ink">{clock(i.at)}</span>
                        {" · "}{ago(i.at, now)}
                        {i.kind === "completed_phone" && i.durationSec ? ` · ${formatDuration(i.durationSec)}` : ""}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
