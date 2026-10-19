"use client";

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { toast } from "sonner";
import { ArrowUpRight, CalendarClock, CalendarPlus, History, PenLine, Phone, X, XCircle } from "lucide-react";
import { CallDoc, CallRun, CallSettingsResponse, CancelEvent } from "@/lib/types";
import { inCallingWindow, isoToZonedInput, nextCallingSlot, windowText, zonedInputToIso } from "@/lib/nurse/window";
import { DEFAULT_RECALL } from "./recall-settings";
import { findingsFor, verdictFor, FindingLevel } from "@/lib/triage";
import { formatDuration, formatPhone } from "@/lib/format";
import { getFollowUpLabel, getStatusLabel } from "@/lib/status";
import { FOLLOW_UP_OPTIONS, NurseActions } from "@/lib/nurse/actions";
import { useCallDetail } from "@/hooks/use-call-detail";
import { TranscriptViewer } from "@/components/transcript-viewer";
import { RecordingPlayer } from "@/components/recording-player";
import { CallTexts } from "@/components/call-texts";
import { SurveyAnswers } from "@/components/survey-answers";
import { cn } from "@/lib/utils";
import { Avatar, Pill, Tone, ago, clock } from "./parts";
import { ClinicDateTimeInput } from "./clinic-time-input";
import { fmtDateTime } from "@/lib/time";

export interface DrawerSubject {
  name: string;
  patientId?: string;
  phone: string;
  procedure?: string;
  tone: Tone;
  /** One line under the name: why this patient is on the list you came from. */
  context?: React.ReactNode;
  call?: CallDoc;
  scheduledFor?: string | null;
  /** A cancelled queued call: the entry to reschedule, and why it was cancelled. */
  entryId?: string;
  cancel?: CancelEvent;
}

const VERDICT_TONE: Record<string, Tone> = {
  emergency: "emergency",
  urgent: "urgent",
  routine: "routine",
  info: "info",
  clear: "good",
  incomplete: "muted",
};

const TIER_TONE: Record<string, Tone> = {
  emergency: "emergency",
  urgent_same_day: "urgent",
  routine_next_business_day: "routine",
  informational: "info",
  none: "good",
};

const LEVEL: Record<FindingLevel, string> = {
  alert: "bg-brand-red/[0.07] ring-brand-red/20 text-brand-red-dark",
  watch: "bg-brand-yellow/20 ring-brand-yellow/40 text-ink",
  normal: "bg-brand/[0.06] ring-brand/15 text-brand-dark",
  unknown: "bg-mist ring-rule text-ink-soft",
  unasked: "bg-mist/60 ring-rule text-ink-muted",
};

type Tab = "summary" | "transcript" | "recording" | "texts";

export function PatientDrawer({
  subject,
  now,
  actions,
  runs = [],
  settings,
  onClose,
  onCallUpdated,
}: {
  subject: DrawerSubject | null;
  now: Date;
  actions: NurseActions;
  /** Scheduled runs a cancelled patient can be put back into. */
  runs?: CallRun[];
  /** Clinic calling hours (the Auto-recall window) limit a reschedule. */
  settings?: CallSettingsResponse | null;
  onClose: () => void;
  /** The follow-up tier was changed; the caller refreshes its data. */
  onCallUpdated?: (call: CallDoc) => void;
}) {
  return (
    <Dialog.Root open={!!subject} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-brand-deep-dark/25 backdrop-blur-[3px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed inset-y-3 right-3 z-50 flex w-[min(42rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-[28px] bg-white/95 shadow-2xl ring-1 ring-black/5 backdrop-blur-2xl data-[state=open]:animate-in data-[state=open]:slide-in-from-right-8 data-[state=open]:fade-in-0">
          {subject ? <Body s={subject} now={now} actions={actions} runs={runs} settings={settings} onClose={onClose} onCallUpdated={onCallUpdated} /> : null}
          <Dialog.Close className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-full bg-white/20 text-white transition-colors hover:bg-white/30">
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Body({
  s,
  now,
  actions,
  runs,
  settings,
  onClose,
  onCallUpdated,
}: {
  s: DrawerSubject;
  now: Date;
  actions: NurseActions;
  runs: CallRun[];
  settings?: CallSettingsResponse | null;
  onClose: () => void;
  onCallUpdated?: (call: CallDoc) => void;
}) {
  // The drawer keeps its own copy so a tier change shows at once, before the
  // stream catches up.
  const [call, setCall] = useState<CallDoc | undefined>(s.call);
  useEffect(() => setCall(s.call), [s.call]);
  const [tab, setTab] = useState<Tab>("summary");
  useEffect(() => setTab("summary"), [s.call?.doc_id, s.name]);

  const verdict = call ? verdictFor(call) : null;
  const findings = call ? findingsFor(call) : [];
  const sid = call?.call_sid ?? call?.doc_id;
  const tabs: { id: Tab; label: string }[] = call
    ? [
        { id: "summary", label: "Summary" },
        { id: "transcript", label: "Transcript" },
        { id: "recording", label: "Recording" },
        { id: "texts", label: "Texts" },
      ]
    : [{ id: "summary", label: "Summary" }];

  return (
    <>
      {/* overflow-hidden: the decorative blob below reaches past the edge, and
          without clipping it the browser scrolls the drawer sideways when a
          form control inside takes focus. */}
      <div className="relative overflow-hidden bg-gradient-to-br from-brand-deep to-brand-deep-dark px-6 pb-5 pt-7 text-white">
        <div className="pointer-events-none absolute -right-10 -top-16 h-48 w-48 rounded-full bg-brand-bright/30 blur-3xl" />
        <div className="relative flex items-center gap-4 pr-10">
          <Avatar name={s.name} tone={s.tone} size="lg" className="bg-white/15 text-white ring-1 ring-white/25" />
          <div className="min-w-0">
            <Dialog.Title className="truncate font-display text-xl font-semibold">{s.name}</Dialog.Title>
            <Dialog.Description className="mt-0.5 text-[13px] text-white/70">
              {[s.patientId, s.procedure].filter(Boolean).join(" · ") || "—"}
            </Dialog.Description>
          </div>
        </div>
        {s.context ? <div className="relative mt-4 text-[13px] text-white/85">{s.context}</div> : null}
        <div className="relative mt-4 flex flex-wrap gap-2">
          <a href={`tel:${s.phone}`} className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-brand-deep transition-transform hover:-translate-y-px">
            <Phone className="h-3.5 w-3.5" /> Call {formatPhone(s.phone)}
          </a>
          {call ? (
            <a href="/classic" className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-4 py-2 text-[13px] font-semibold text-white ring-1 ring-white/20 transition-colors hover:bg-white/15">
              Full record <ArrowUpRight className="h-3.5 w-3.5" />
            </a>
          ) : null}
        </div>
        {tabs.length > 1 ? (
          <div className="relative mt-5 flex gap-1 rounded-full bg-black/20 p-1" role="tablist">
            {tabs.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={cn("flex-1 rounded-full py-1.5 text-[13px] font-semibold transition-colors",
                  tab === t.id ? "bg-white text-brand-deep" : "text-white/70 hover:text-white")}
              >
                {t.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-6 py-6">
        {tab === "summary" ? (
          <>
            {s.cancel && s.entryId ? (
              <CancelledSection cancel={s.cancel} entryId={s.entryId} runs={runs} now={now} actions={actions} settings={settings} onDone={onClose} />
            ) : null}

            {s.scheduledFor ? (
              <Section title="Scheduled">
                <div className="flex items-center gap-2 text-[14px] text-ink">
                  <CalendarClock className="h-4 w-4 text-brand" />
                  {fmtDateTime(s.scheduledFor, { weekday: "long", month: "short", day: "numeric", sep: " at " })}
                </div>
              </Section>
            ) : null}

            {call?.review?.status === "signed_off" ? (
              <div className="rounded-2xl bg-brand/[0.07] px-4 py-3 text-[13px] text-ink ring-1 ring-inset ring-brand/20">
                <span className="font-semibold">{call.review.resolved ? "Follow-up resolved and signed off" : "Signed off"}</span>
                {" "}by {call.review.by ?? "nurse"}{call.review.at ? ` · ${fmtDateTime(call.review.at, { month: "short", day: "numeric", year: "numeric" })}` : ""}
                {call.review.note ? <div className="mt-1 text-ink-soft">{call.review.note}</div> : null}
              </div>
            ) : call?.review?.status === "superseded" ? (
              <div className="rounded-2xl bg-mist/60 px-4 py-3 text-[13px] text-ink-soft ring-1 ring-inset ring-black/[0.06]">
                {call.review.resolved
                  ? <>Resolved together with a later report by {call.review.by ?? "nurse"}</>
                  : <>Closed: a later report for this patient replaced it and was signed off by {call.review.by ?? "nurse"}</>}
                {call.review.at ? ` · ${fmtDateTime(call.review.at, { month: "short", day: "numeric", year: "numeric" })}` : ""}.
                {call.review.resolved ? " Sent to the EHR as closed with that note." : " Not sent to the EHR."}
                {call.review.resolved && call.review.note ? <div className="mt-1 text-ink-soft">{call.review.note}</div> : null}
              </div>
            ) : call?.review?.status === "reopened" ? (
              <div className="rounded-2xl bg-brand-yellow/20 px-4 py-3 text-[13px] text-ink ring-1 ring-inset ring-brand-yellow/40">
                Sign-off reopened: {call.review.reopened_because ?? "the follow-up changed after it was signed"}. Needs review again.
              </div>
            ) : null}

            {call ? (
              <FollowUpControl
                call={call}
                actions={actions}
                onChanged={(c) => { setCall(c); onCallUpdated?.(c); }}
              />
            ) : null}

            {verdict ? (
              <Section title="Agent’s assessment">
                <div className="rounded-2xl bg-mist/60 p-4 ring-1 ring-black/[0.04]">
                  <Pill tone={VERDICT_TONE[verdict.tone] ?? "muted"}>{verdict.headline}</Pill>
                  <p className="mt-2 text-[14px] leading-relaxed text-ink">{verdict.reason}</p>
                </div>
              </Section>
            ) : null}

            {findings.length ? (
              <Section title="Top answers">
                <div className="grid grid-cols-2 gap-2">
                  {findings.map((f) => (
                    <div key={f.key} className={cn("rounded-2xl p-3 ring-1 ring-inset", LEVEL[f.level])}>
                      <div className="text-[11px] font-semibold uppercase tracking-wide opacity-70">{f.label}</div>
                      <div className="mt-0.5 font-display text-[15px] font-semibold">{f.value}</div>
                      {f.detail ? <div className="mt-0.5 text-[12px] opacity-80">{f.detail}</div> : null}
                    </div>
                  ))}
                </div>
              </Section>
            ) : null}

            {call ? (
              <Section title="Last contact">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[13px]">
                  <Item k="Result" v={call.channel === "sms" ? "Answered by text" : getStatusLabel(call.call_status)} />
                  <Item k="When" v={call.ended_at ? `${clock(call.ended_at)} · ${ago(call.ended_at, now)}` : "—"} />
                  <Item k="Duration" v={call.channel === "sms" ? "—" : formatDuration(call.duration_seconds)} />
                  <Item k="Identity check" v={call.dob_verification_result ?? "—"} />
                </dl>
              </Section>
            ) : null}

            {call?.outcome ? (
              <Section title="All answers">
                <SurveyAnswers call={call} />
              </Section>
            ) : null}
          </>
        ) : null}

        {tab !== "summary" && call && sid ? (
          actions.live ? (
            <Evidence sid={sid} call={call} tab={tab} />
          ) : (
            <p className="rounded-2xl bg-mist/60 p-4 text-[13px] text-ink-soft">
              Sample data has no {tab === "texts" ? "text thread" : tab}. Switch to live data to see the real one.
            </p>
          )
        ) : null}
      </div>
    </>
  );
}

/** Transcript, recording and texts come from the backend on demand. */
function Evidence({ sid, call, tab }: { sid: string; call: CallDoc; tab: Tab }) {
  const d = useCallDetail(sid);
  useEffect(() => {
    if (tab === "transcript" && !d.turns.length && !d.transcriptLoading && d.transcriptAvailable === null) d.reloadTranscript();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, sid]);

  if (tab === "transcript") {
    return (
      <div className="flex min-h-[20rem] flex-col">
        <TranscriptViewer
          call={d.call ?? call}
          turns={d.turns}
          loading={d.transcriptLoading}
          error={d.transcriptError}
          available={d.transcriptAvailable}
          reason={d.transcriptReason}
          onReload={d.reloadTranscript}
          fill
        />
      </div>
    );
  }
  if (tab === "recording") {
    const hasRecording = !!(d.call ?? call).recording_uri || !!(d.call ?? call).recording_url;
    return (
      <Section title="Call recording">
        <RecordingPlayer callSid={sid} hasRecording={hasRecording} />
      </Section>
    );
  }
  return (
    <Section title="Text conversation for this call">
      <CallTexts phone={call.to} callSid={sid} />
    </Section>
  );
}

/** Why the call was cancelled, and the way back onto the queue. */
function CancelledSection({
  cancel,
  entryId,
  runs,
  now,
  actions,
  settings,
  onDone,
}: {
  cancel: CancelEvent;
  entryId: string;
  runs: CallRun[];
  now: Date;
  actions: NurseActions;
  settings?: CallSettingsResponse | null;
  onDone: () => void;
}) {
  // Calling hours: the Auto-recall window, in the clinic's time zone. The
  // proxy enforces the same rule; this keeps out-of-hours choices off screen.
  const hours = settings?.recall ?? DEFAULT_RECALL;
  const tz = settings?.timezone ?? "America/New_York";
  const upcoming = runs
    .filter((r) => r.status === "scheduled" && new Date(r.scheduled_for).getTime() > now.getTime())
    .filter((r) => inCallingWindow(r.scheduled_for, hours, tz))
    .sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
  const [mode, setMode] = useState<"existing" | "new">(upcoming.length ? "existing" : "new");
  const [runId, setRunId] = useState(upcoming[0]?.run_id ?? "");
  // Default: the next allowed quarter-hour at least an hour out.
  const [when, setWhen] = useState(() =>
    isoToZonedInput(nextCallingSlot(Date.now() + 60 * 60_000, hours, tz) ?? new Date(Date.now() + 3_600_000).toISOString(), tz));
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const iso = zonedInputToIso(when, tz);
  const inPast = iso !== null && new Date(iso).getTime() <= Date.now();
  const outOfHours = iso !== null && !inPast && !inCallingWindow(iso, hours, tz);
  const valid = mode === "existing" ? !!runId : iso !== null && !inPast && !outOfHours;

  const save = async () => {
    setBusy(true);
    try {
      await actions.rescheduleEntry(entryId, mode === "existing" ? { run_id: runId } : { scheduled_for: iso!, label: label.trim() || null });
      if (actions.live) toast.success("Back on the call queue");
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reschedule.");
    } finally {
      setBusy(false);
    }
  };

  const runText = (r: CallRun) =>
    `${fmtDateTime(r.scheduled_for, { weekday: "short", month: "short", day: "numeric", sep: " · " })}` +
    `${r.label ? ` — ${r.label}` : ""} (${r.entry_ids.length} ${r.entry_ids.length === 1 ? "patient" : "patients"})`;

  return (
    <Section title="Cancelled">
      <div className="rounded-2xl bg-brand-orange/10 p-4 ring-1 ring-inset ring-brand-orange/30">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-ink">
          <XCircle className="h-4 w-4 text-brand-coral-dark" />
          Call cancelled {fmtDateTime(cancel.at, { weekday: "short", month: "short", day: "numeric" })}
          {cancel.by ? <span className="font-normal text-ink-soft">by {cancel.by}</span> : null}
        </div>
        <p className="mt-2 text-[14px] leading-relaxed text-ink">{cancel.reason}</p>
        {cancel.run_scheduled_for ? (
          <p className="mt-1 text-[12px] text-ink-muted">
            Was planned for {fmtDateTime(cancel.run_scheduled_for, { weekday: "short", month: "short", day: "numeric" })}.
          </p>
        ) : null}
      </div>

      <div className="mt-3 rounded-2xl bg-white p-4 ring-1 ring-black/[0.06]">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-ink">
          <CalendarPlus className="h-4 w-4 text-brand" /> Reschedule
        </div>
        <div className="mt-3 space-y-2">
          <label className={cn("flex items-start gap-3 rounded-xl px-3 py-2 ring-1 ring-inset",
            mode === "existing" ? "bg-brand/[0.07] ring-brand/40" : "ring-black/[0.06]", !upcoming.length && "opacity-50")}>
            <input type="radio" className="mt-1 accent-brand" checked={mode === "existing"} disabled={!upcoming.length} onChange={() => setMode("existing")} />
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-ink">Add to a scheduled run</span>
              {upcoming.length ? (
                <select
                  value={runId}
                  onChange={(e) => { setRunId(e.target.value); setMode("existing"); }}
                  className="mt-1.5 w-full rounded-lg bg-mist/60 px-2 py-1.5 text-[13px] text-ink ring-1 ring-inset ring-black/[0.06]"
                >
                  {upcoming.map((r) => <option key={r.run_id} value={r.run_id}>{runText(r)}</option>)}
                </select>
              ) : (
                <span className="block text-[12px] text-ink-muted">No upcoming runs inside calling hours.</span>
              )}
            </span>
          </label>
          <label className={cn("flex items-start gap-3 rounded-xl px-3 py-2 ring-1 ring-inset",
            mode === "new" ? "bg-brand/[0.07] ring-brand/40" : "ring-black/[0.06]")}>
            <input type="radio" className="mt-1 accent-brand" checked={mode === "new"} onChange={() => setMode("new")} />
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-ink">New run at</span>
              <span className="mt-1.5 flex flex-wrap gap-2">
                <ClinicDateTimeInput value={when} onChange={(v) => { setWhen(v); setMode("new"); }} label="New run"
                  inputClassName="rounded-lg bg-mist/60 px-2 py-1.5 text-[13px] text-ink ring-1 ring-inset ring-black/[0.06]" />
                <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="Name (optional)"
                  className="min-w-0 flex-1 rounded-lg bg-mist/60 px-2 py-1.5 text-[13px] text-ink ring-1 ring-inset ring-black/[0.06]" />
              </span>
              {mode === "new" && iso && Intl.DateTimeFormat().resolvedOptions().timeZone !== tz ? (
                <span className="mt-1 block text-[11px] text-ink-muted">
                  = {fmtDateTime(iso, { timeZone: tz, weekday: "short", sep: " " })} clinic time
                </span>
              ) : null}
              {mode === "new" && inPast ? (
                <span className="mt-1 block text-[11px] text-brand-red-dark">That time has passed.</span>
              ) : mode === "new" && outOfHours ? (
                <span className="mt-1 block text-[11px] text-brand-red-dark">
                  Outside calling hours ({windowText(hours)}).{" "}
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => { const n = nextCallingSlot(new Date(iso!).getTime(), hours, tz); if (n) setWhen(isoToZonedInput(n, tz)); }}
                  >
                    Use the next allowed time
                  </button>
                </span>
              ) : null}
            </span>
          </label>
        </div>
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-[11px] text-ink-muted">Calling hours: {windowText(hours)} clinic time (set under Auto-recall)</span>
          <button
            onClick={save}
            disabled={!valid || busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white hover:bg-brand-dark disabled:opacity-40"
          >
            <CalendarPlus className="h-3.5 w-3.5" /> {busy ? "Saving…" : "Put back on the queue"}
          </button>
        </div>
      </div>
    </Section>
  );
}

/**
 * The nurse's own disposition. Changing it requires a reason; the agent's
 * verdict is kept alongside and every change is listed.
 */
function FollowUpControl({
  call,
  actions,
  onChanged,
}: {
  call: CallDoc;
  actions: NurseActions;
  onChanged: (c: CallDoc) => void;
}) {
  const current = call.follow_up ?? "";
  const [editing, setEditing] = useState(false);
  const [tier, setTier] = useState(current);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTier(current); setReason(""); setEditing(false); }, [current, call.doc_id]);

  const sid = call.call_sid ?? call.doc_id;
  const history = call.follow_up_history ?? [];
  const reasonOk = reason.trim().length >= 5;
  const changed = tier !== current;

  const save = async () => {
    if (!changed || !reasonOk) return;
    setBusy(true);
    try {
      const updated = await actions.setFollowUp(sid, { follow_up: tier, reason: reason.trim() });
      onChanged(updated);
      toast.success(`Follow-up set to ${getFollowUpLabel(tier)}`);
      setEditing(false);
      setReason("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not change the follow-up.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Follow-up">
      <div className="rounded-2xl bg-white p-4 ring-1 ring-black/[0.06]">
        <div className="flex flex-wrap items-center gap-2">
          {current ? (
            <Pill tone={TIER_TONE[current] ?? "muted"} icon={call.follow_up_override ? PenLine : undefined}>
              {getFollowUpLabel(current)}
            </Pill>
          ) : (
            <Pill tone="muted">Not assessed</Pill>
          )}
          {call.follow_up_override ? (
            <span className="text-[12px] text-ink-muted">
              set by {call.follow_up_override.set_by ?? "nurse"} · agent said {getFollowUpLabel(call.agent_follow_up ?? undefined)}
            </span>
          ) : null}
          {!editing ? (
            <button
              onClick={() => setEditing(true)}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-brand/10 px-3 py-1.5 text-[12px] font-semibold text-brand-dark hover:bg-brand hover:text-white"
            >
              <PenLine className="h-3.5 w-3.5" /> Change
            </button>
          ) : null}
        </div>

        {editing ? (
          <div className="mt-4 space-y-3">
            <div className="grid gap-1.5">
              {FOLLOW_UP_OPTIONS.map((o) => (
                <label
                  key={o.value}
                  className={cn("flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2 ring-1 ring-inset transition-colors",
                    tier === o.value ? "bg-brand/[0.07] ring-brand/40" : "ring-black/[0.06] hover:bg-mist/60")}
                >
                  <input type="radio" name="tier" value={o.value} checked={tier === o.value} onChange={() => setTier(o.value)} className="mt-1 accent-brand" />
                  <span>
                    <span className="block text-[13px] font-semibold text-ink">{o.label}{o.value === current ? " (current)" : ""}</span>
                    <span className="block text-[12px] text-ink-muted">{o.hint}</span>
                  </span>
                </label>
              ))}
            </div>
            <label className="block">
              <span className="text-[12px] font-semibold text-ink-soft">Reason for the change <span className="text-brand-red-dark">*</span></span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder="e.g. Called the patient back; pain controlled after medication adjustment."
                className="mt-1 w-full rounded-xl bg-mist/60 px-3 py-2 text-[13px] text-ink ring-1 ring-inset ring-black/[0.06] placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-brand/40"
              />
              {!reasonOk && reason.length > 0 ? <span className="text-[11px] text-brand-red-dark">Say why, in a few words at least.</span> : null}
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={() => { setEditing(false); setTier(current); setReason(""); }} className="rounded-full px-3.5 py-1.5 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
                Cancel
              </button>
              <button
                onClick={save}
                disabled={!changed || !reasonOk || busy}
                title={!changed ? "Pick a different tier" : !reasonOk ? "A reason is required" : undefined}
                className="rounded-full bg-brand px-3.5 py-1.5 text-[13px] font-semibold text-white hover:bg-brand-dark disabled:opacity-40"
              >
                {busy ? "Saving…" : "Save change"}
              </button>
            </div>
          </div>
        ) : null}

        {history.length ? (
          <details className="mt-3 text-[12px] text-ink-soft">
            <summary className="flex cursor-pointer items-center gap-1.5 font-semibold">
              <History className="h-3.5 w-3.5" /> {history.length} {history.length === 1 ? "change" : "changes"}
            </summary>
            <ol className="mt-2 space-y-1.5 border-l-2 border-rule pl-3">
              {[...history].reverse().map((h, i) => (
                <li key={`${h.at}-${i}`}>
                  <span className="font-semibold text-ink">{getFollowUpLabel(h.from ?? undefined)} → {getFollowUpLabel(h.to)}</span>
                  <span className="text-ink-muted"> · {h.set_by ?? "nurse"} · {fmtDateTime(h.at, { month: "short", day: "numeric", year: "numeric" })}</span>
                  <div className="text-ink">{h.reason}</div>
                </li>
              ))}
            </ol>
          </details>
        ) : null}
      </div>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-muted">{title}</h4>
      {children}
    </section>
  );
}

function Item({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-ink-muted">{k}</dt>
      <dd className="mt-0.5 font-medium text-ink first-letter:uppercase">{v}</dd>
    </div>
  );
}
