"use client";

import { useEffect, useState } from "react";
import { MessageSquare } from "lucide-react";

import { fetchMessages, FollowUpError, type SmsMessage } from "@/lib/followups";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTime } from "@/lib/format";

/**
 * The text exchange that belongs to ONE call.
 *
 * A patient who never picks up can still reschedule or complete the whole
 * questionnaire by text, and that conversation is part of this call's record
 * — not a separate thing that only exists in the Follow-ups tab.
 *
 * Scoped by `callSid`, not just the handset: a number gets called more than
 * once, and grouping by phone alone splices every conversation it has ever
 * had into one run-on thread.
 */
export function CallTexts({
  phone,
  callSid,
  hideWhenEmpty = false,
}: {
  phone?: string;
  callSid?: string;
  /** Render nothing at all when there are no texts, rather than an empty
   *  state. Used where texts are the exception (a normal voice call), so
   *  every transcript does not carry a box explaining their absence. */
  hideWhenEmpty?: boolean;
}) {
  const [messages, setMessages] = useState<SmsMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!phone || !callSid) {
      setMessages([]);
      return;
    }
    let alive = true;
    setMessages(null);
    setError(null);
    fetchMessages(phone, callSid)
      .then((r) => alive && setMessages(r.messages ?? []))
      .catch((e) => {
        if (!alive) return;
        // The SMS service is optional. A dashboard without it configured
        // should say so plainly rather than showing a broken panel.
        setError(
          e instanceof FollowUpError && e.status === 503
            ? "The SMS follow-up service is not configured for this dashboard."
            : e instanceof Error
              ? e.message
              : "Could not load the text conversation."
        );
        setMessages([]);
      });
    return () => {
      alive = false;
    };
  }, [phone, callSid]);

  if (messages === null && hideWhenEmpty) return null;

  if (messages === null) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-10 w-1/2 ml-auto" />
        <Skeleton className="h-10 w-3/4" />
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-ink-soft italic">{error}</p>;
  }

  if (messages.length === 0 && hideWhenEmpty) return null;

  if (messages.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-rule p-4 text-sm text-ink-soft italic">
        No texts were exchanged for this call. A follow-up text is sent
        automatically when a call goes unanswered.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-xs text-ink-soft">
        <MessageSquare className="h-3.5 w-3.5" />
        {messages.length} message{messages.length === 1 ? "" : "s"} for this call
      </p>
      <ol className="space-y-2">
        {messages.map((m) => {
          const outbound = m.direction === "outbound";
          return (
            <li
              key={m.id}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                outbound ? "bg-mist text-ink" : "ml-auto bg-brand-blue/10 text-ink"
              }`}
            >
              <span className="flex items-baseline justify-between gap-3 text-[11px] text-ink-soft mb-0.5">
                <span className="uppercase tracking-wide">
                  {outbound ? "Us" : "Patient"}
                </span>
                {/* Timestamps make the pacing legible: how long a patient
                 * took to answer is clinically interesting, and it is the
                 * only way to tell a prompt reply from a next-day one. */}
                <span className="tabular-nums">{formatTime(m.at)}</span>
              </span>
              {m.body}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
