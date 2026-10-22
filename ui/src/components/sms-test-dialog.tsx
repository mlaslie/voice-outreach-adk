"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquareText, RotateCcw, Send, ShieldAlert } from "lucide-react";

import { api, ApiError, type SmsTestReplyResponse, type SmsTestStartResponse } from "@/lib/api";
import type { SmsMessage } from "@/lib/followups";
import { formatTime } from "@/lib/format";
import {
  DEFAULT_LANGUAGE,
  textTestWording,
  type LanguageInfo,
  type LanguagePackInfo,
} from "@/lib/languages";
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

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message : fallback;
}

/**
 * "1950-03-15" -> "03/15/1950", the way a patient would type it; day first
 * ("15/03/1950") for a patient in another language, as their texts ask.
 */
function typedDate(iso: string, dayFirst = false): string {
  const [y, m, d] = iso.split("-");
  if (!(y && m && d)) return iso;
  return dayFirst ? `${d}/${m}/${y}` : `${m}/${d}/${y}`;
}

/**
 * Play the patient in the text follow-up, end to end, without a phone.
 *
 * The backend stages a missed call for the fictional test patient and runs
 * every reply through the real inbound handler, so the menu, the birthdate
 * check, the questionnaire and triage are all the real thing. It is always
 * simulated, even on a service that sends real texts: the conversation runs on
 * the dry-run provider in its own namespace, so nothing is sent to the phone
 * and nothing it files reaches the console, the EHR outbox or a real patient's
 * conversation.
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
  const [language, setLanguage] = useState(DEFAULT_LANGUAGE);
  const [langs, setLangs] = useState<LanguageInfo[]>([]);
  const [packs, setPacks] = useState<LanguagePackInfo[] | undefined>(undefined);
  const [session, setSession] = useState<SmsTestStartResponse | null>(null);
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  const [state, setState] = useState<string | null>(null);
  const [report, setReport] = useState<SmsTestReplyResponse["report"]>(null);
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

  // The 24 languages and which have text wording (a draft is allowed here).
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api
      .getLanguages()
      .then((r) => {
        if (!alive) return;
        setLangs(r.languages);
        setPacks(r.packs);
      })
      .catch(() => {
        if (alive) setLangs([]);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  // No polling: a simulated conversation queues nothing, so every message it
  // produces comes back in the reply's response.

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  function reset() {
    setSession(null);
    setMessages([]);
    setState(null);
    setReport(null);
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
      const r = await api.startSmsTest(phone, diabetic, language);
      setSession(r);
      setMessages(r.messages);
      setState(r.state);
      setReport(null);
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
      setReport(r.report ?? null);
    } catch (err) {
      setError(errorText(err, "Could not send the reply."));
    } finally {
      setBusy(false);
    }
  }

  const closed = state === "closed" || state === "expired" || state === "opted_out";
  const chosen = textTestWording(language, packs);
  const wordingLabel = session?.wording?.label || "";
  const dayFirst = (session?.wording?.language ?? DEFAULT_LANGUAGE) !== DEFAULT_LANGUAGE
    && session?.wording?.language !== "en-IN";

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
            follow-up and a finished questionnaire is triaged, but the test stays here: it never
            reaches the console or a real patient&apos;s conversation.
          </DialogDescription>
        </DialogHeader>

        <div
          role="status"
          className="flex items-start gap-2 rounded-lg border border-brand-orange bg-brand-orange/15 px-3 py-2 text-sm font-medium text-ink"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          Simulated: nothing is sent to the phone.
        </div>

        {!session ? (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="sms-test-phone">Approved number</Label>
              {numbers === null ? (
                <p className="text-sm text-ink-soft">Loading…</p>
              ) : numbers.length === 0 ? (
                <p className="text-sm text-ink-soft">
                  No approved numbers. Add one in the admin console, under Authorized numbers.
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
            <div className="space-y-1.5">
              <Label htmlFor="sms-test-language">Patient language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger id="sms-test-language">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(langs.length
                    ? langs
                    : [{ code: DEFAULT_LANGUAGE, name: "English (US)", native: "English (US)", available: true }]
                  ).map((l) => {
                    const w = textTestWording(l.code, packs);
                    return (
                      <SelectItem key={l.code} value={l.code} disabled={w === null}>
                        {l.name}
                        {w === null ? " (no text wording yet)" : w.label ? " (draft wording)" : ""}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              {chosen?.label ? (
                <p className="text-xs font-medium text-brand-red-dark">
                  {chosen.label}: a machine draft no translator has reviewed. Real texts never use
                  it; this test sends nothing.
                </p>
              ) : null}
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
              Fictional test patient: Jordan Rivera, TEST-0001, born{" "}
              {language === DEFAULT_LANGUAGE || language === "en-IN" ? "03/15/1950" : "15/03/1950"}.
              Starting a test replaces any earlier test conversation on this number.
            </p>
            <Button onClick={start} disabled={busy || !phone || chosen === null} className="w-full">
              {busy ? "Starting…" : "Start"}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2 text-xs text-ink-soft">
              <span>
                {session.patient.patient_name} · born{" "}
                {typedDate(session.patient.patient_dob, dayFirst)}
                {session.wording && session.wording.language !== DEFAULT_LANGUAGE
                  ? ` · ${session.wording.name ?? session.wording.language}`
                  : ""}
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
            {wordingLabel ? (
              <p
                role="note"
                className="rounded-lg border border-brand-red/40 bg-brand-red/10 px-3 py-1.5 text-xs font-semibold text-brand-red-dark"
              >
                {wordingLabel}. Real texts in this language stay off until a translator reviews it
                and it is approved.
              </p>
            ) : null}
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
            {report ? (
              <p className="text-xs text-ink-soft">
                Report filed in the simulator
                {report.follow_up ? ` (${report.follow_up.replace(/_/g, " ")})` : ""}
                {report.follow_up_reason ? `: ${report.follow_up_reason}` : "."} Test reports stay
                out of the console.
              </p>
            ) : null}
          </div>
        )}

        {error ? <p className="rounded bg-brand-red/10 p-2 text-sm text-brand-red-dark">{error}</p> : null}
      </DialogContent>
    </Dialog>
  );
}
