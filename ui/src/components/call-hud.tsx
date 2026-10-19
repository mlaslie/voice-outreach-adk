"use client";

import { useEffect } from "react";
import {
  AlertTriangle,
  Check,
  CircleAlert,
  CircleHelp,
  Clock,
  Minus,
  Phone,
  ShieldCheck,
  X,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "./status-chip";
import { RecordingPlayer } from "./recording-player";
import { TranscriptViewer } from "./transcript-viewer";
import { CallTexts } from "./call-texts";
import { CompletionFlag } from "./completion-flag";
import { PatientQuestionsSection } from "./patient-questions";
import { useCallDetail } from "@/hooks/use-call-detail";
import { CallDoc } from "@/lib/types";
import { formatPhone, formatDuration, formatTime, relativeTime } from "@/lib/format";
import { Finding, FindingLevel, findingsFor, summarize, verdictFor } from "@/lib/triage";
import { FollowUpReasonList } from "@/components/follow-up-reasons";
import { reasonsFor } from "@/lib/follow-up-reasons";

/**
 * Full-screen triage view for a single call.
 *
 * Replaces the four-tab drawer. The tabs made a nurse hunt: the verdict was
 * in one, the vitals in another, the evidence in a third. Here everything is
 * on one screen, ordered by how urgently it needs to be read —
 * verdict, then abnormal findings, then supporting detail.
 */

interface CallHudProps {
  callSid: string | null;
  liveDoc?: CallDoc;
  onOpenChange: (open: boolean) => void;
}

/* ---------- verdict banner ------------------------------------------- */

const VERDICT_TONE = {
  emergency: {
    band: "bg-brand-red text-white",
    icon: AlertTriangle,
    sub: "text-white/85",
  },
  urgent: {
    band: "bg-brand-coral text-white",
    icon: CircleAlert,
    sub: "text-white/85",
  },
  routine: {
    band: "bg-brand-yellow text-ink",
    icon: Clock,
    sub: "text-ink/70",
  },
  info: {
    band: "bg-mist-cool text-ink",
    icon: Minus,
    sub: "text-ink-soft",
  },
  clear: {
    band: "bg-brand text-white",
    icon: Check,
    sub: "text-white/85",
  },
  incomplete: {
    band: "bg-ink-soft text-white",
    icon: CircleHelp,
    sub: "text-white/80",
  },
} as const;

/* ---------- findings -------------------------------------------------- */

const LEVEL_STYLE: Record<FindingLevel, { card: string; label: string; Icon: typeof Check }> = {
  alert:   { card: "border-brand-red/50 bg-brand-red/5",     label: "text-brand-red-dark",   Icon: AlertTriangle },
  watch:   { card: "border-brand-orange bg-brand-orange/10", label: "text-ink",              Icon: CircleAlert },
  normal:  { card: "border-rule bg-white",                   label: "text-ink-soft",         Icon: Check },
  unknown: { card: "border-rule bg-mist",                    label: "text-ink-soft",         Icon: CircleHelp },
  unasked: { card: "border-dashed border-rule bg-mist/50",   label: "text-ink-muted",        Icon: Minus },
};

function FindingCard({ f }: { f: Finding }) {
  const st = LEVEL_STYLE[f.level];
  const loud = f.level === "alert" || f.level === "watch";
  return (
    <div className={`rounded-lg border p-3 ${st.card}`}>
      <div className="flex items-center gap-1.5">
        <st.Icon className={`h-3.5 w-3.5 shrink-0 ${st.label}`} />
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${st.label}`}>
          {f.label}
        </span>
      </div>
      <p
        className={`mt-1 leading-tight ${
          loud ? "text-lg font-bold text-ink" : "text-sm text-ink"
        }`}
      >
        {f.value}
      </p>
      {f.detail && (
        <p className="mt-0.5 text-xs text-ink-soft leading-snug">{f.detail}</p>
      )}
    </div>
  );
}

/* ---------- pain scale ------------------------------------------------ */

function PainScale({ severity }: { severity: number }) {
  return (
    <div className="flex items-center gap-2" aria-label={`Pain ${severity} out of 10`}>
      <div className="flex gap-0.5" aria-hidden>
        {Array.from({ length: 10 }, (_, i) => {
          const on = i < severity;
          const tone =
            severity >= 8 ? "bg-brand-red" : severity >= 4 ? "bg-brand-orange" : "bg-brand";
          return (
            <span
              key={i}
              className={`h-5 w-2.5 rounded-sm ${on ? tone : "bg-rule"}`}
            />
          );
        })}
      </div>
      <span className="text-sm font-semibold text-ink tabular-nums">{severity}/10</span>
    </div>
  );
}

/* ---------- small pieces ---------------------------------------------- */

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className="text-sm text-ink mt-0.5">{value ?? "—"}</dd>
    </div>
  );
}

function TimelineRow({ label, time }: { label: string; time?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-b border-rule last:border-0">
      <span className="text-xs text-ink-soft">{label}</span>
      <span className="text-xs text-ink tabular-nums">{time ? formatTime(time) : "—"}</span>
    </div>
  );
}

/* ---------- the HUD --------------------------------------------------- */

export function CallHud({ callSid, liveDoc, onOpenChange }: CallHudProps) {
  const {
    call,
    turns,
    loading,
    transcriptLoading,
    transcriptError,
    transcriptAvailable,
    transcriptReason,
    reloadTranscript,
  } = useCallDetail(callSid);

  const doc = liveDoc ?? call;

  useEffect(() => {
    if (!callSid) return;
    reloadTranscript();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callSid]);

  const verdict = doc ? verdictFor(doc) : null;
  const findings = doc ? findingsFor(doc) : [];
  const summary = summarize(findings);
  const tone = verdict ? VERDICT_TONE[verdict.tone] : VERDICT_TONE.incomplete;
  const VerdictIcon = tone.icon;

  const ctx = (doc?.patient_context ?? {}) as Record<string, unknown>;
  const severity = (() => {
    const v = (doc?.survey_responses as Record<string, unknown> | undefined)?.pain_severity;
    const num = typeof v === "number" ? v : Number(v);
    return Number.isFinite(num) ? num : undefined;
  })();

  return (
    <Dialog open={!!callSid} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-none w-[97vw] h-[95vh] top-[2.5vh] translate-y-0 flex flex-col gap-0 p-0 overflow-hidden">
        {loading && !doc ? (
          <div className="p-8 space-y-4">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : !doc ? (
          <p className="p-8 text-sm text-ink-soft">Call not found.</p>
        ) : (
          <>
            {/* ---- Verdict: the one thing to read first ---- */}
            <div className={`px-6 py-4 shrink-0 ${tone.band}`}>
              <div className="flex items-start justify-between gap-6">
                <div className="flex items-start gap-3 min-w-0">
                  <VerdictIcon className="h-8 w-8 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <DialogTitle className="text-2xl font-bold leading-tight">
                      {verdict?.headline}
                    </DialogTitle>
                    {reasonsFor(doc).length ? (
                      <FollowUpReasonList call={doc} variant="band" className="mt-2" />
                    ) : (
                      <p className={`text-sm mt-0.5 ${tone.sub}`}>{verdict?.reason}</p>
                    )}
                  </div>
                </div>
                <div className="text-right shrink-0 pr-8">
                  <p className="text-lg font-semibold leading-tight">
                    {doc.patient_name ?? formatPhone(doc.to)}
                  </p>
                  <p className={`text-xs ${tone.sub}`}>
                    {doc.patient_id && `${doc.patient_id} · `}
                    {doc.initiated_at && relativeTime(doc.initiated_at)}
                  </p>
                </div>
              </div>
            </div>

            {/* ---- Body ---- */}
            <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1.35fr_1fr] gap-0 overflow-hidden">
              {/* Left: triage-critical */}
              <div className="overflow-y-auto p-6 space-y-5 border-r border-rule">
                {/* How this was completed, before anything else. The call
                 * record alone is misleading: a questionnaire finished over
                 * SMS lands as `success` with a full set of answers, which
                 * reads exactly like a call the patient picked up. */}
                <CompletionFlag call={doc} />

                {/* Patient facts */}
                <section>
                  <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    <Fact label="Phone" value={<span className="font-mono">{formatPhone(doc.to)}</span>} />
                    <Fact
                      label="Procedure"
                      value={
                        (ctx.operation_name as string) ?? <span className="text-ink-muted">—</span>
                      }
                    />
                    <Fact label="Procedure date" value={(ctx.operation_date as string) ?? "—"} />
                    <Fact
                      label="Diabetic"
                      value={
                        ctx.is_diabetic === true ? (
                          <span className="font-semibold text-ink">Yes</span>
                        ) : (
                          "No"
                        )
                      }
                    />
                  </dl>
                </section>

                {/* The patient's own questions: each needs a nurse callback. */}
                <PatientQuestionsSection call={doc} />

                {/* Findings */}
                <section>
                  <div className="flex items-baseline justify-between mb-2">
                    <h3 className="text-sm font-semibold text-ink">What the patient reported</h3>
                    {summary && (
                      <p
                        className={`text-xs ${
                          summary.tone === "alert"
                            ? "text-brand-red-dark font-semibold"
                            : summary.tone === "watch"
                            ? "text-ink font-medium"
                            : summary.tone === "muted"
                            ? "text-ink-muted"
                            : "text-brand-dark"
                        }`}
                      >
                        {summary.text}
                      </p>
                    )}
                  </div>

                  {findings.length === 0 ? (
                    <p className="text-sm text-ink-soft italic border border-dashed border-rule rounded-lg p-4">
                      No answers were collected on this call.
                    </p>
                  ) : (
                    <>
                      {severity !== undefined && (
                        <div className="mb-3 rounded-lg border border-rule bg-white p-3">
                          <p className="text-[11px] uppercase tracking-wide text-ink-muted mb-1.5">
                            Pain severity
                          </p>
                          <PainScale severity={severity} />
                        </div>
                      )}
                      <div className="grid grid-cols-2 xl:grid-cols-3 gap-2.5">
                        {findings.map((f) => (
                          <FindingCard key={f.key} f={f} />
                        ))}
                      </div>
                    </>
                  )}
                </section>

                {/* Call facts + timeline + recording */}
                <section className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div>
                    <h3 className="text-sm font-semibold text-ink mb-2">Call</h3>
                    <div className="flex items-center gap-2 mb-2">
                      <StatusChip callStatus={doc.call_status} />
                      <span className="text-xs text-ink-soft">
                        {formatDuration(doc.duration_seconds)}
                        {doc.outcome && ` · ${doc.outcome.replace(/_/g, " ")}`}
                      </span>
                    </div>
                    <TimelineRow label="Initiated" time={doc.initiated_at} />
                    <TimelineRow label="Ringing" time={doc.ringing_at} />
                    <TimelineRow label="Answered" time={doc.answered_at} />
                    <TimelineRow label="Ended" time={doc.ended_at} />
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-ink mb-2">Recording</h3>
                    <RecordingPlayer callSid={doc.doc_id} hasRecording={!!doc.recording_uri} />
                    <p className="mt-2 text-[11px] text-ink-muted font-mono break-all">
                      {doc.doc_id}
                    </p>
                  </div>
                </section>
              </div>

              {/* Right: the evidence */}
              <div className="overflow-hidden flex flex-col p-6 bg-mist/40 min-h-0">
                {/* The evidence, whichever way the patient answered. A
                 * questionnaire completed over SMS has no voice transcript
                 * at all, so showing only the voice panel left this column
                 * empty for exactly the calls that had the most to say. */}
                {doc.channel === "sms" ? (
                  <div className="flex-1 min-h-0 overflow-y-auto">
                    <CallTexts phone={doc.to} callSid={doc.doc_id} />
                  </div>
                ) : (
                  <>
                    <TranscriptViewer
                      fill
                      call={doc}
                      turns={turns}
                      loading={transcriptLoading}
                      error={transcriptError}
                      available={transcriptAvailable}
                      reason={transcriptReason}
                      onReload={reloadTranscript}
                    />
                    <div className="shrink-0 mt-4">
                      <CallTexts phone={doc.to} callSid={doc.doc_id} hideWhenEmpty />
                    </div>
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
