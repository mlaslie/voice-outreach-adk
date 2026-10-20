"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Activity, CalendarClock, MessageSquare, TriangleAlert, X } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { AdminActions, AdminReads } from "@/lib/nurse/actions";
import {
  FollowUpSession,
  ScheduledCallback,
  SmsMessage,
  Tone as FollowUpTone,
  isCancellable,
  isOpen,
  isOverdue,
  sessionLabel,
  sortSessions,
  summarize,
  upcoming,
} from "@/lib/followups";
import { CallDoc } from "@/lib/types";
import { formatPhone } from "@/lib/format";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { TextThread } from "@/components/call-texts";
import { EmptyState, FilterChips, Panel, Pill, Tone, ago, countdown } from "@/components/nurse/parts";

/**
 * Admin → Messaging: the text conversations and booked callbacks, ported
 * from the classic dashboard's Follow-ups tab (components/followups-view.tsx,
 * retired). The 24-hour text limit is its own card above these.
 */

const TONE_OF: Record<FollowUpTone, Tone> = { wait: "warn", good: "good", alert: "emergency", muted: "muted" };

type ConvFilter = "open" | "all";

export function TextConversationsPanel({
  sessions,
  calls,
  error,
  reads,
  now,
  onOpenCall,
  timeZone,
}: {
  sessions: FollowUpSession[];
  /** Recent calls, to name the call a conversation came from. */
  calls: CallDoc[];
  /** The last read failed: the list shows the last known state. */
  error?: string | null;
  reads: Pick<AdminReads, "messages">;
  now: Date;
  /** Open the call in Call diagnostics. */
  onOpenCall?: (callId: string) => void;
  /** Clinic time zone for dates. */
  timeZone?: string;
}) {
  const [filter, setFilter] = useState<ConvFilter>("open");
  const [thread, setThread] = useState<FollowUpSession | null>(null);
  const callById = useMemo(() => new Map(calls.map((c) => [c.call_sid ?? c.doc_id, c])), [calls]);

  const sorted = useMemo(() => sortSessions(sessions), [sessions]);
  const open = sorted.filter((s) => isOpen(s.state));
  const shown = filter === "open" ? open : sorted;
  const stats = summarize(sessions, [], now);

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <MessageSquare className="h-4 w-4 text-brand" />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Text conversations</h3>
          <p className="text-[12px] text-ink-muted">
            Follow-up texts after a missed call. {stats.waiting ? `${stats.waiting} waiting on the patient.` : "Nobody is waiting on a reply."}
            {stats.optedOut ? ` ${stats.optedOut} opted out.` : ""} Open one to read the thread.
          </p>
        </div>
        <FilterChips<ConvFilter>
          options={[
            { value: "open", label: "Open", count: open.length, tone: "warn" },
            { value: "all", label: "All", count: sorted.length },
          ]}
          value={filter}
          onChange={setFilter}
        />
      </div>
      {error ? (
        <p className="flex items-center gap-2 border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark">
          <TriangleAlert className="h-4 w-4 shrink-0" /> Showing the last known state: {error}
        </p>
      ) : null}
      {shown.length === 0 ? (
        <EmptyState
          title={filter === "open" ? "No open conversations" : "No text conversations yet"}
          hint={filter === "open" && sorted.length
            ? "Finished, expired and opted-out conversations are under All."
            : "A follow-up text is sent automatically when a call goes unanswered."}
        />
      ) : (
        <ul className="divide-y divide-black/[0.05]">
          {shown.map((s) => {
            const label = sessionLabel(s.state);
            const call = s.call_sid ? callById.get(s.call_sid) : undefined;
            const last = s.updated_at ?? s.last_inbound_at ?? s.last_invite_at ?? null;
            return (
              <li key={`${s.phone}-${s.call_sid ?? ""}`}>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-3 transition-colors hover:bg-brand/[0.03]">
                  <button
                    type="button"
                    onClick={() => setThread(s)}
                    className="flex min-w-0 flex-1 items-center gap-4 text-left"
                    aria-label={`Open the text thread with ${s.patient_name || formatPhone(s.phone)}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-display text-[14px] font-semibold text-ink">
                        {s.patient_name || "Unknown patient"}
                      </div>
                      <div className="text-[12px] tabular-nums text-ink-muted">
                        {formatPhone(s.phone)}
                        {s.message_count ? ` · ${s.message_count} ${s.message_count === 1 ? "text" : "texts"}` : ""}
                      </div>
                    </div>
                    <Pill tone={TONE_OF[label.tone]}>{label.text}</Pill>
                    <span className="hidden w-24 shrink-0 text-right text-[12px] text-ink-soft sm:block" title={last ? fmtDateTime(last, { month: "short", day: "numeric", timeZone }) : undefined}>
                      {last ? ago(last, now) : "—"}
                    </span>
                  </button>
                  <span className="w-full text-[12px] text-ink-muted sm:w-48 sm:text-right">
                    {s.call_sid ? (
                      <>
                        From the call{" "}
                        {call ? fmtDateTime(call.initiated_at, { weekday: "short", timeZone }) : `…${s.call_sid.slice(-6)}`}
                        {onOpenCall ? (
                          <button
                            type="button"
                            onClick={() => onOpenCall(s.call_sid!)}
                            className="ml-1.5 inline-flex items-center gap-1 font-semibold text-brand-dark hover:underline"
                            title="Open this call in Call diagnostics"
                          >
                            <Activity className="h-3 w-3" /> Diagnose
                          </button>
                        ) : null}
                      </>
                    ) : "No call on record"}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <ThreadDialog session={thread} reads={reads} onClose={() => setThread(null)} />
    </Panel>
  );
}

/** One conversation's texts, scoped to the call it followed. */
function ThreadDialog({
  session,
  reads,
  onClose,
}: {
  session: FollowUpSession | null;
  reads: Pick<AdminReads, "messages">;
  onClose: () => void;
}) {
  const phone = session?.phone ?? null;
  const callSid = session?.call_sid ?? null;
  const expected = session?.message_count ?? 0;
  const [messages, setMessages] = useState<SmsMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!phone) return;
    let alive = true;
    setMessages(null);
    setError(null);
    reads.messages(phone, callSid)
      .then((m) => { if (alive) setMessages(m); })
      .catch((e) => { if (alive) { setError(e instanceof Error ? e.message : "Could not load the thread."); setMessages([]); } });
    return () => { alive = false; };
  }, [phone, callSid, reads]);

  const label = session ? sessionLabel(session.state) : null;

  return (
    <Dialog.Root open={!!phone} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-brand-deep-dark/40 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-[24px] bg-white shadow-2xl">
          <div className="flex items-start gap-3 border-b border-black/[0.06] px-5 py-4">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate font-display text-[17px] font-semibold text-ink">
                {session?.patient_name || (phone ? formatPhone(phone) : "Conversation")}
              </Dialog.Title>
              <Dialog.Description className="text-[12px] tabular-nums text-ink-muted">
                {phone ? formatPhone(phone) : ""}
                {session?.patient_id ? ` · ${session.patient_id}` : ""}
              </Dialog.Description>
            </div>
            {label ? <Pill tone={TONE_OF[label.tone]}>{label.text}</Pill> : null}
            <Dialog.Close className="grid h-8 w-8 place-items-center rounded-full text-ink-soft hover:bg-mist hover:text-ink" aria-label="Close">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto bg-mist/30 p-5">
            {messages === null ? (
              <div className="space-y-2">
                <Skeleton className="h-10 w-2/3" />
                <Skeleton className="ml-auto h-10 w-1/2" />
                <Skeleton className="h-10 w-3/4" />
              </div>
            ) : error ? (
              <p className="text-[13px] text-brand-red-dark">{error}</p>
            ) : messages.length === 0 ? (
              expected > 0 ? (
                // The session counted texts it sent, but none were logged:
                // a recording failure, not a quiet conversation.
                <p className="flex items-start gap-2 rounded-xl bg-brand-orange/15 p-3 text-[13px] text-ink">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  {expected} {expected === 1 ? "text was" : "texts were"} sent in this conversation, but none were recorded.
                  The thread is missing, not empty: check the service logs.
                </p>
              ) : (
                <p className="text-[13px] italic text-ink-soft">No texts yet.</p>
              )
            ) : (
              <TextThread messages={messages} caption={`${messages.length} ${messages.length === 1 ? "text" : "texts"} after this call`} />
            )}
            {session?.scheduled_for_text ? (
              <p className="mt-4 text-[12px] text-ink-muted">Patient asked for: &ldquo;{session.scheduled_for_text}&rdquo;</p>
            ) : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Upcoming callbacks patients booked by text, each cancellable after a confirm. */
export function BookedCallbacksPanel({
  callbacks,
  actions,
  now,
  timeZone,
}: {
  callbacks: ScheduledCallback[];
  actions: Pick<AdminActions, "live" | "cancelCallback">;
  now: Date;
  timeZone?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const booked = useMemo(() => upcoming(callbacks), [callbacks]);
  const overdue = booked.filter((c) => isOverdue(c, now)).length;

  const cancel = async (cb: ScheduledCallback) => {
    const who = cb.patient_name || formatPhone(cb.phone);
    const when = fmtDateTime(cb.run_at, { weekday: "short", month: "short", day: "numeric", timeZone });
    if (!window.confirm(`Cancel the callback to ${who} on ${when}? Nobody will be called, and the patient is not told.`)) return;
    setBusy(cb.callback_id);
    try {
      await actions.cancelCallback(cb.callback_id);
      if (actions.live) toast.success(`Callback to ${who} cancelled`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not cancel the callback.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <CalendarClock className="h-4 w-4 text-brand" />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Booked callbacks</h3>
          <p className="text-[12px] text-ink-muted">Calls patients asked for by text, soonest first. The scheduler dials them at the booked time.</p>
        </div>
        <span className="text-[12px] text-ink-muted">{booked.length} upcoming</span>
      </div>
      {overdue ? (
        <p className="flex items-center gap-2 border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark">
          <TriangleAlert className="h-4 w-4 shrink-0" />
          {overdue === 1 ? "A callback is" : `${overdue} callbacks are`} past due: the scheduler may not have run. Check the Cloud Scheduler job before assuming the patient was called.
        </p>
      ) : null}
      {booked.length === 0 ? (
        <EmptyState title="No callbacks booked" hint="When a patient picks a call-back time by text, it shows here." />
      ) : (
        <ul className="divide-y divide-black/[0.05]">
          {booked.map((cb) => {
            const late = isOverdue(cb, now);
            const cd = countdown(cb.run_at, now);
            return (
              <li key={cb.callback_id} className={cn("flex flex-wrap items-center gap-3 px-5 py-3", busy === cb.callback_id && "opacity-50")}>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-display text-[14px] font-semibold text-ink">{cb.patient_name || "Unknown patient"}</div>
                  <div className="text-[12px] tabular-nums text-ink-muted">{formatPhone(cb.phone)}</div>
                </div>
                <div className="text-right">
                  <div className="text-[13px] font-semibold tabular-nums text-ink">
                    {fmtDateTime(cb.run_at, { weekday: "short", month: "short", day: "numeric", timeZone })}
                  </div>
                  <div className={cn("text-[12px]", late ? "text-brand-red-dark" : "text-ink-muted")}>{cd.text}</div>
                </div>
                {late ? <Pill tone="emergency">Overdue</Pill>
                  : cb.status === "dialing" ? <Pill tone="good" pulse>Calling</Pill>
                  : <Pill tone="good">Booked</Pill>}
                {isCancellable(cb) ? (
                  <button
                    type="button"
                    onClick={() => cancel(cb)}
                    disabled={busy !== null}
                    className="inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:bg-brand-red/[0.06] hover:text-brand-red-dark disabled:opacity-40"
                  >
                    <X className="h-3.5 w-3.5" /> Cancel
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
