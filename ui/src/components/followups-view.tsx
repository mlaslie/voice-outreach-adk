"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarClock, MessageSquare, RefreshCw, TriangleAlert, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { MovedToAdmin } from "./moved-to-admin";
import { useFollowUps } from "@/hooks/use-followups";
import {
  FollowUpSettings,
  FollowUpSession,
  Label as ToneLabel,
  SmsMessage,
  Tone,
  callbackLabel,
  cancelCallback,
  fetchMessages,
  fetchSettings,
  isCancellable,
  isOverdue,
  sessionLabel,
  sortSessions,
  openConversations,
  isOpen,
  summarize,
  upcoming,
} from "@/lib/followups";
import { formatPhone, formatTime, relativeTime } from "@/lib/format";
import { fmtDateTime } from "@/lib/time";

/**
 * The Follow-ups tab: who is mid-conversation, who has a callback booked,
 * and who has opted out.
 *
 * Ordered by what a nurse has to act on. Bookings come first because they
 * are a commitment to a patient at a specific time; conversations second
 * because they resolve themselves; opt-outs surface inline because they
 * change who may be contacted at all.
 */

const TONE_CHIP: Record<Tone, string> = {
  wait: "bg-brand-orange/15 text-ink border-brand-orange/40",
  good: "bg-brand/10 text-brand-dark border-brand/30",
  alert: "bg-brand-red/10 text-brand-red-dark border-brand-red/40",
  muted: "bg-mist text-ink-soft border-rule",
};

function Chip({ label }: { label: ToneLabel }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${
        TONE_CHIP[label.tone]
      }`}
    >
      {label.text}
    </span>
  );
}

function Stat({
  value,
  label,
  tone = "muted",
}: {
  value: number;
  label: string;
  tone?: Tone;
}) {
  const accent =
    tone === "alert"
      ? "text-brand-red-dark"
      : tone === "wait"
        ? "text-ink"
        : tone === "good"
          ? "text-brand-dark"
          : "text-ink-soft";
  return (
    <div className="rounded-lg border border-rule bg-white px-4 py-3">
      <p className={`text-2xl font-bold tabular-nums leading-none ${accent}`}>{value}</p>
      <p className="mt-1 text-xs text-ink-soft">{label}</p>
    </div>
  );
}

/* ---- message thread --------------------------------------------------- */

function ThreadDialog({
  session,
  onClose,
}: {
  session: FollowUpSession | null;
  onClose: () => void;
}) {
  const phone = session?.phone ?? null;
  const patientName = session?.patient_name;
  const expected = session?.message_count ?? 0;
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!phone) return;
    let alive = true;
    setLoading(true);
    setError(null);
    fetchMessages(phone, session?.call_sid)
      .then((r) => alive && setMessages(r.messages ?? []))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Failed to load."))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [phone, session?.call_sid]);

  return (
    <Dialog open={!!phone} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden">
        <div className="border-b border-rule px-5 py-3 shrink-0">
          <DialogTitle className="text-base font-semibold">
            {patientName || (phone ? formatPhone(phone) : "Conversation")}
          </DialogTitle>
          {patientName && phone && (
            <p className="text-xs text-ink-soft font-mono">{formatPhone(phone)}</p>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-3 bg-mist/40">
          {loading ? (
            <>
              <Skeleton className="h-14 w-3/4" />
              <Skeleton className="h-10 w-1/2 ml-auto" />
            </>
          ) : error ? (
            <p className="text-sm text-brand-red-dark">{error}</p>
          ) : messages.length === 0 ? (
            expected > 0 ? (
              // The session counted messages it sent, but none were logged.
              // Saying "no messages yet" here would report a recording
              // failure as a quiet conversation.
              <div className="flex items-start gap-2 rounded-lg border border-brand-orange/40 bg-brand-orange/10 p-3">
                <TriangleAlert className="h-4 w-4 shrink-0 text-ink mt-0.5" />
                <p className="text-sm text-ink">
                  {expected} message{expected === 1 ? " was" : "s were"} sent in this
                  conversation, but none were recorded. The transcript is missing,
                  not empty — check the service logs.
                </p>
              </div>
            ) : (
              <p className="text-sm text-ink-soft italic">
                No messages yet. One is sent automatically when a call goes
                unanswered.
              </p>
            )
          ) : (
            messages.map((m) => {
              const out = m.direction === "outbound";
              return (
                <div key={m.id} className={`flex ${out ? "justify-start" : "justify-end"}`}>
                  <div className="max-w-[85%]">
                    <div
                      className={`rounded-2xl px-3.5 py-2 text-sm leading-snug ${
                        out
                          ? "bg-white border border-rule text-ink rounded-bl-sm"
                          : "bg-brand text-white rounded-br-sm"
                      }`}
                    >
                      {m.body}
                    </div>
                    <p
                      className={`mt-1 text-[11px] text-ink-muted ${out ? "" : "text-right"}`}
                    >
                      {out ? "Sent" : "Patient"} · {formatTime(m.at)}
                    </p>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ---- the tab ---------------------------------------------------------- */

export function FollowUpsView() {
  const { sessions, callbacks, loading, error, refresh } = useFollowUps();
  const [thread, setThread] = useState<FollowUpSession | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [settings, setSettings] = useState<FollowUpSettings | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  useEffect(() => {
    fetchSettings()
      .then((r) => setSettings(r.settings))
      .catch(() => setSettings(null));   // hide the control rather than guess
  }, []);

  const booked = useMemo(() => upcoming(callbacks), [callbacks]);
  // Follow-ups is a worklist: only conversations still waiting on someone.
  // A completed questionnaire has nothing outstanding and lives on the call
  // record instead.
  const ordered = useMemo(
    () => sortSessions(openConversations(sessions, showClosed)),
    [sessions, showClosed]
  );
  const hiddenCount = useMemo(
    () => sessions.length - openConversations(sessions).length,
    [sessions]
  );
  const stats = useMemo(() => summarize(sessions, callbacks), [sessions, callbacks]);

  async function onCancel(id: string) {
    setCancelling(id);
    try {
      await cancelCallback(id);
      toast.success("Callback cancelled.");
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not cancel.");
    } finally {
      setCancelling(null);
    }
  }

  if (loading && sessions.length === 0 && callbacks.length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-brand-red/40 bg-brand-red/5 px-4 py-3">
          <TriangleAlert className="h-4 w-4 shrink-0 text-brand-red-dark mt-0.5" />
          <div className="text-sm">
            <p className="font-medium text-brand-red-dark">
              Showing the last known state
            </p>
            <p className="text-ink-soft">{error}</p>
          </div>
        </div>
      )}

      {settings && !settings.invite_cooldown_enabled && (
        <div className="flex items-start gap-2 rounded-lg border border-brand-orange/50 bg-brand-orange/10 px-4 py-3">
          <TriangleAlert className="h-4 w-4 shrink-0 text-ink mt-0.5" />
          <div className="text-sm flex-1">
            <p className="font-medium text-ink">Follow-up cooldown is off</p>
            <p className="text-ink-soft">
              A number can be re-invited as soon as another call goes
              unanswered, instead of once per 24 hours. Testing only — turn it
              back on before contacting anyone real.
            </p>
          </div>
          <span className="shrink-0 self-center">
            <MovedToAdmin what="The text limit switch" />
          </span>
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 flex-1">
          <Stat value={stats.waiting} label="Waiting for a reply" tone="wait" />
          <Stat value={stats.booked} label="Callbacks booked" tone="good" />
          <Stat value={stats.overdue} label="Overdue" tone={stats.overdue ? "alert" : "muted"} />
          <Stat value={stats.optedOut} label="Opted out" tone={stats.optedOut ? "alert" : "muted"} />
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={refresh}>
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* ---- Scheduled callbacks ---- */}
      <section>
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink mb-2">
          <CalendarClock className="h-4 w-4 text-ink-soft" />
          Scheduled callbacks
        </h2>
        {booked.length === 0 ? (
          <p className="rounded-lg border border-dashed border-rule p-4 text-sm text-ink-soft italic">
            No callbacks booked.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-rule bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th className="px-4 py-2 font-medium text-ink-soft">Patient</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Phone</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Calling at</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Status</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {booked.map((cb) => {
                  const overdue = isOverdue(cb);
                  return (
                    <tr key={cb.callback_id} className="border-b border-rule last:border-0">
                      <td className="px-4 py-2.5 text-ink">
                        {cb.patient_name || <span className="text-ink-muted">—</span>}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs text-ink-soft">
                        {formatPhone(cb.phone)}
                      </td>
                      <td className="px-4 py-2.5 tabular-nums">
                        {fmtDateTime(cb.run_at, { weekday: "short" })}
                        <span className="ml-2 text-xs text-ink-muted">
                          {relativeTime(cb.run_at)}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        {overdue ? (
                          <Chip label={{ text: "Overdue", tone: "alert" }} />
                        ) : (
                          <Chip label={callbackLabel(cb.status)} />
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {isCancellable(cb) && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={cancelling === cb.callback_id}
                            onClick={() => onCancel(cb.callback_id)}
                          >
                            <X className="h-3.5 w-3.5 mr-1" />
                            Cancel
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {stats.overdue > 0 && (
          <p className="mt-2 text-xs text-brand-red-dark">
            An overdue callback means the scheduler has not run. Check the Cloud
            Scheduler job before assuming the patient was called.
          </p>
        )}
      </section>

      {/* ---- Conversations ---- */}
      <section>
        <div className="flex items-center justify-between gap-3 mb-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <MessageSquare className="h-4 w-4 text-ink-soft" />
            {showClosed ? "Text conversations" : "Open conversations"}
          </h2>
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setShowClosed((v) => !v)}
              className="text-xs text-ink-soft underline underline-offset-2 hover:text-ink"
            >
              {showClosed
                ? "Show only open"
                : `Show all ${sessions.length}`}
            </button>
          )}
        </div>
        {ordered.length === 0 ? (
          <p className="rounded-lg border border-dashed border-rule p-4 text-sm text-ink-soft italic">
            {hiddenCount > 0
              ? "Nothing needs attention. Booked callbacks are listed above; finished conversations are on the call record."
              : "No follow-up texts have been sent yet. One is sent automatically when a call goes unanswered."}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-rule bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th className="px-4 py-2 font-medium text-ink-soft">Patient</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Phone</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">State</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Booked for</th>
                  <th className="px-4 py-2 font-medium text-ink-soft">Last activity</th>
                </tr>
              </thead>
              <tbody>
                {ordered.map((s) => (
                  <tr
                    key={s.phone}
                    onClick={() => setThread(s)}
                    className="border-b border-rule last:border-0 cursor-pointer hover:bg-mist/60"
                  >
                    <td className="px-4 py-2.5 text-ink">
                      {s.patient_name || <span className="text-ink-muted">—</span>}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-soft">
                      {formatPhone(s.phone)}
                    </td>
                    <td className="px-4 py-2.5">
                      <Chip label={sessionLabel(s.state)} />
                    </td>
                    <td className="px-4 py-2.5 text-ink-soft">
                      {s.scheduled_for
                        ? <span title={s.scheduled_for_text ? `Patient was told: “${s.scheduled_for_text}”` : undefined}>
                            {fmtDateTime(s.scheduled_for, { weekday: "short", month: "short", day: "numeric" })}
                          </span>
                        : s.scheduled_for_text || <span className="text-ink-muted">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-ink-soft text-xs">
                      {s.updated_at ? relativeTime(s.updated_at) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ThreadDialog session={thread} onClose={() => setThread(null)} />
    </div>
  );
}
