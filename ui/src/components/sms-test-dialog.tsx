"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquareText, RotateCcw, Send, ShieldAlert } from "lucide-react";

import { api, ApiError, type SmsTestStartResponse } from "@/lib/api";
import { fetchMessages, type SmsMessage } from "@/lib/followups";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AllowlistEntry } from "@/lib/types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const E164 = /^\+[1-9][0-9]{6,14}$/;
const POLL_MS = 4000;

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message : fallback;
}

/** "1950-03-15" -> "03/15/1950", the way a patient would type it. */
function usDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${m}/${d}/${y}` : iso;
}

/**
 * Play the patient in the text follow-up, end to end, without a phone.
 *
 * The backend stages a missed call for the fictional test patient and runs
 * every reply through the real inbound handler, so the menu, the birthdate
 * check, the questionnaire, triage and the filed report are all the real
 * thing. It refuses unless SMS is in dry-run mode: nothing leaves the system.
 */
export function SmsTestDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [numbers, setNumbers] = useState<AllowlistEntry[] | null>(null);
  const [phone, setPhone] = useState("");
  const [diabetic, setDiabetic] = useState(false);
  const [session, setSession] = useState<SmsTestStartResponse | null>(null);
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  const [state, setState] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLLIElement>(null);

  // The allowlisted numbers (exact entries only: a prefix is not a handset).
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api
      .getAllowlist()
      .then((r) => {
        if (!alive) return;
        const exact = r.entries.filter((e) => E164.test(e.phone));
        setNumbers(exact);
        setPhone((p) => p || exact[0]?.phone || "");
      })
      .catch((e) => {
        if (!alive) return;
        setNumbers([]);
        setError(errorText(e, "Could not load the approved numbers."));
      });
    return () => {
      alive = false;
    };
  }, [open]);

  const refresh = useCallback(async () => {
    if (!session) return;
    try {
      const r = await fetchMessages(session.phone, session.call_sid);
      setMessages(r.messages ?? []);
    } catch {
      // keep what we have; the next poll tries again
    }
  }, [session]);

  // Follow-on messages can arrive after a reply (a queued filing, a "cannot
  // call back" notice), so poll the thread while a test is running.
  useEffect(() => {
    if (!open || !session) return;
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [open, session, refresh]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  function reset() {
    setSession(null);
    setMessages([]);
    setState(null);
    setReportId(null);
    setDraft("");
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  async function start() {
    if (!E164.test(phone)) {
      setError("Pick an approved number.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.startSmsTest(phone, diabetic);
      setSession(r);
      setMessages(r.messages);
      setState(r.state);
      setReportId(null);
    } catch (e) {
      setError(errorText(e, "Could not start the test."));
    } finally {
      setBusy(false);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const body = draft.trim();
    if (!session || !body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.replySmsTest(session.phone, body);
      setDraft("");
      setMessages(r.messages);
      setState(r.state);
      setReportId(r.report_id);
    } catch (err) {
      setError(errorText(err, "Could not send the reply."));
    } finally {
      setBusy(false);
    }
  }

  const closed = state === "closed" || state === "expired" || state === "opted_out";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageSquareText className="h-5 w-5" />
            Test by text
          </DialogTitle>
          <DialogDescription>
            Play the patient in the missed-call text follow-up. Replies run through the real
            follow-up, and a finished questionnaire is filed like any other report.
          </DialogDescription>
        </DialogHeader>

        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-brand-orange bg-brand-orange/15 px-3 py-2 text-sm font-medium text-ink"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          Dry run — nothing is sent to a phone.
        </div>

        {!session ? (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="sms-test-phone">Approved number</Label>
              {numbers === null ? (
                <p className="text-sm text-ink-soft">Loading…</p>
              ) : numbers.length === 0 ? (
                <p className="text-sm text-ink-soft">
                  No approved numbers. Add one under Settings → Manage approved numbers.
                </p>
              ) : (
                <Select value={phone} onValueChange={setPhone}>
                  <SelectTrigger id="sms-test-phone">
                    <SelectValue placeholder="Pick a number" />
                  </SelectTrigger>
                  <SelectContent>
                    {numbers.map((n) => (
                      <SelectItem key={n.phone} value={n.phone}>
                        {n.phone}
                        {n.label && n.label !== "configured" ? ` · ${n.label}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <div className="flex items-center gap-2.5">
              <Checkbox
                id="sms-test-diabetic"
                checked={diabetic}
                onCheckedChange={(v) => setDiabetic(v === true)}
              />
              <Label htmlFor="sms-test-diabetic" className="cursor-pointer font-normal">
                Diabetic (adds the blood-sugar question)
              </Label>
            </div>
            <p className="text-xs text-ink-soft">
              Fictional test patient: Jordan Rivera, TEST-0001, born 03/15/1950. Starting a test
              replaces any earlier test conversation on this number.
            </p>
            <Button onClick={start} disabled={busy || !phone} className="w-full">
              {busy ? "Starting…" : "Start"}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2 text-xs text-ink-soft">
              <span>
                {session.patient.patient_name} · born {usDate(session.patient.patient_dob)}
                {session.patient.is_diabetic ? " · diabetic" : ""}
              </span>
              <button
                type="button"
                onClick={reset}
                className="inline-flex items-center gap-1 font-medium text-ink-soft hover:text-ink"
              >
                <RotateCcw className="h-3.5 w-3.5" /> New test
              </button>
            </div>
            {!session.survey_enabled ? (
              <p className="text-xs text-ink-soft">
                Answering the questions by text (option 3) is switched off on this deployment.
              </p>
            ) : null}

            {/* The handset. The patient's own messages sit on the right. */}
            <div className="rounded-[28px] border-[6px] border-ink/80 bg-mist/40 shadow-inner">
              <div className="border-b border-rule px-4 py-2 text-center text-xs font-semibold text-ink">
                Care team
                <span className="block text-[10px] font-normal text-ink-soft">{session.phone}</span>
              </div>
              <ol className="h-80 space-y-2 overflow-y-auto px-3 py-3" aria-live="polite">
                {messages.map((m) => {
                  const fromUs = m.direction === "outbound";
                  return (
                    <li key={m.id} className={cn("flex flex-col", fromUs ? "items-start" : "items-end")}>
                      <span
                        className={cn(
                          "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm",
                          fromUs ? "rounded-bl-sm bg-white text-ink ring-1 ring-black/[0.06]" : "rounded-br-sm bg-brand text-white",
                        )}
                      >
                        {m.body}
                      </span>
                      <span className="mt-0.5 px-1 text-[10px] tabular-nums text-ink-soft">{formatTime(m.at)}</span>
                    </li>
                  );
                })}
                <li ref={bottom} aria-hidden />
              </ol>
              <form onSubmit={send} className="flex items-center gap-2 border-t border-rule p-2">
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  maxLength={1600}
                  placeholder={closed ? "Conversation closed" : "Text message"}
                  aria-label="Reply as the patient"
                  className="h-9 min-w-0 flex-1 rounded-full bg-white px-3.5 text-sm text-ink ring-1 ring-black/[0.08] placeholder:text-ink-soft focus:outline-none focus:ring-2 focus:ring-brand/40"
                />
                <button
                  type="submit"
                  disabled={busy || !draft.trim()}
                  aria-label="Send reply"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-brand text-white disabled:opacity-40"
                >
                  <Send className="h-4 w-4" />
                </button>
              </form>
            </div>
            {reportId ? (
              <p className="text-xs text-ink-soft">
                Report filed. It appears on the test call ({session.call_sid}) in the console.
              </p>
            ) : null}
          </div>
        )}

        {error ? <p className="rounded bg-brand-red/10 p-2 text-sm text-brand-red-dark">{error}</p> : null}
      </DialogContent>
    </Dialog>
  );
}
