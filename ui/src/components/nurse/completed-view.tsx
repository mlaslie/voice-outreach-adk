"use client";

import { useEffect, useState } from "react";
import {
  Ban,
  Check,
  ListChecks,
  Trash2,
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
import { CallDoc } from "@/lib/types";
import { NurseActions } from "@/lib/nurse/actions";
import {
  DeletePlan, effectiveSelection, planDeletion, selectAllState, toggleAll, toggleOne,
} from "@/lib/nurse/delete-calls";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { DeleteCallsDialog } from "./delete-calls-dialog";

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
  actions,
  latestCall,
  onRemoved,
}: {
  items: CompletedItem[];
  now: Date;
  onOpen: (item: CompletedItem) => void;
  /** Calls whose open follow-up is past its callback window. */
  overdueCallIds?: Set<string>;
  preset?: CompletedPreset | null;
  /** Needed for multi-select delete; without it there is no Select button. */
  actions?: NurseActions;
  /** The freshest copy of a call, to skip one that is in progress again. */
  latestCall?: (docId: string) => CallDoc | undefined;
  /** Calls the backend confirmed gone (rows leave the board at once). */
  onRemoved?: (callIds: string[]) => void;
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

  // Multi-select delete (cleaning up demo calls). Only the selected rows that
  // are SHOWN count, so a row hidden by a filter or search is never deleted.
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [plan, setPlan] = useState<DeletePlan | null>(null);
  const chosen = effectiveSelection(selected, rows);
  const allState = selectAllState(selected, rows);
  const stopSelecting = () => { setSelecting(false); setSelected(new Set()); };
  const askDelete = () => {
    const ids = new Set(chosen);
    setPlan(planDeletion(rows.filter((r) => ids.has(r.id)), latestCall));
  };
  const removed = (callIds: string[]) => {
    if (!callIds.length) return;
    setSelected((s) => { const next = new Set(s); callIds.forEach((id) => next.delete(id)); return next; });
    onRemoved?.(callIds);
  };

  return (
    // Room under the list for the fixed action bar while selecting.
    <div className={cn("space-y-4", selecting && "pb-24")}>
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
        {actions ? (
          <button
            type="button"
            aria-pressed={selecting}
            onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
            className={cn("ml-auto inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold ring-1 transition-colors",
              selecting ? "bg-brand-deep text-white ring-brand-deep" : "bg-white/70 text-ink-soft ring-black/[0.06] hover:text-ink")}
          >
            <ListChecks className="h-4 w-4" aria-hidden />
            {selecting ? "Selecting" : "Select"}
          </button>
        ) : null}
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
              const isSel = selecting && selected.has(i.id);
              return (
                <li key={i.id}>
                  <button
                    type="button"
                    onClick={() => (selecting ? setSelected((s) => toggleOne(s, i.id)) : onOpen(i))}
                    {...(selecting
                      ? { role: "checkbox", "aria-checked": isSel, "aria-label": `Select ${i.patientName}, ${k.label}, ${clock(i.at)}` }
                      : {})}
                    className={cn("grid w-full grid-cols-[1fr_auto] items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-brand/[0.035] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/50",
                      isSel && "bg-brand-red/[0.05] hover:bg-brand-red/[0.07]")}
                  >
                    <div className="flex min-w-0 items-center gap-3.5">
                      {selecting ? (
                        <span
                          aria-hidden
                          className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1 transition-colors",
                            isSel ? "bg-brand-red text-white ring-brand-red" : "bg-white ring-black/25")}
                        >
                          {isSel ? <Check className="h-3.5 w-3.5" /> : null}
                        </span>
                      ) : null}
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

      {selecting ? (
        <div
          role="toolbar"
          aria-label="Selected calls"
          className="fixed inset-x-3 bottom-[5.5rem] z-40 mx-auto flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl bg-white/95 px-4 py-2.5 shadow-xl ring-1 ring-black/[0.08] backdrop-blur lg:bottom-6 lg:left-[calc(17rem+3rem)] lg:right-6"
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); stopSelecting(); } }}
        >
          <label className="flex cursor-pointer items-center gap-2 text-[13px] font-semibold text-ink">
            <Checkbox
              checked={allState}
              onCheckedChange={() => setSelected((s) => toggleAll(s, rows))}
              disabled={!rows.length}
              aria-label={allState === true ? `Clear all ${rows.length} shown` : `Select all ${rows.length} shown`}
            />
            Select all ({rows.length})
          </label>
          <span role="status" aria-live="polite" className="text-[13px] tabular-nums text-ink-soft">
            {chosen.length} selected
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} disabled={!chosen.length}>
              Clear
            </Button>
            <Button size="sm" variant="destructive" onClick={askDelete} disabled={!chosen.length}>
              <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              Delete {chosen.length}
            </Button>
            <Button size="sm" variant="secondary" onClick={stopSelecting}>
              Done
            </Button>
          </div>
        </div>
      ) : null}

      {actions ? (
        <DeleteCallsDialog
          plan={plan}
          actions={actions}
          onOpenChange={(open) => { if (!open) setPlan(null); }}
          onRemoved={removed}
        />
      ) : null}
    </div>
  );
}
