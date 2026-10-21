"use client";

import { useId, useState } from "react";
import { toast } from "sonner";
import { BookOpenCheck, MessageCircleQuestion, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import { CallDoc, PatientQuestion, ShadowDraft, ShadowReview, ShadowVerdict } from "@/lib/types";
import {
  MAX_REVIEW_NOTE,
  PRIORITY_LABEL,
  PRIORITY_TONE,
  QuestionPill,
  VERDICT_LABEL,
  answerSource,
  categoryLabel,
  questionEntries,
  questionHandling,
  questionPill,
  questionSummary,
} from "@/lib/patient-questions";
import { shortReason } from "@/lib/knowledge";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/nurse/parts";
import { SourceChips, SupportScore } from "@/components/knowledge-sources";

/** Rate one question's shadow draft; `index` is its position on the call. */
export type ShadowReviewHandler = (index: number, body: { verdict: ShadowVerdict; note?: string }) => Promise<void>;

/**
 * "Patient questions — nurse follow-up required": each question in the
 * patient's own words, immediate first, with category and priority chips.
 * Renders nothing when the patient said no, or the call predates the
 * question (the findings grid already says "None" / "Not asked").
 *
 * With the knowledge base on (docs/KNOWLEDGE-PLAN.md), a question the agent
 * answered from approved clinic Q&A shows the answer as spoken and its
 * source, so a nurse can correct it. In shadow mode, the agent's draft (never
 * said to the patient) shows in its own block for a nurse to rate with
 * `onReview`; without it the rating is read-only. Questions without the new
 * fields look exactly as before.
 */
export function PatientQuestionsSection({
  call,
  className,
  onReview,
  timeZone,
}: {
  call: CallDoc;
  className?: string;
  onReview?: ShadowReviewHandler;
  timeZone?: string;
}) {
  const s = questionSummary(call);
  const entries = questionEntries(call);
  if (!s.count && !s.noneCaptured) return null;
  const loud = s.immediate > 0;
  const allAnswered = s.count > 0 && s.forNurse === 0;

  return (
    <section
      aria-label="Patient questions"
      className={cn(
        "rounded-2xl p-4 ring-1 ring-inset",
        loud
          ? "bg-brand-coral/[0.07] ring-brand-coral/30"
          : allAnswered
            ? "bg-brand/[0.06] ring-brand/25"
            : "bg-brand-yellow/15 ring-brand-yellow/40",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <MessageCircleQuestion className={cn("h-4 w-4", loud ? "text-brand-coral-dark" : "text-ink")} />
        <h4 className="text-[13px] font-semibold text-ink">
          {allAnswered ? "Patient questions — answered by the agent" : "Patient questions — nurse follow-up required"}
        </h4>
        {s.count ? (
          <span className="ml-auto text-[12px] text-ink-soft">
            {s.count} {s.count === 1 ? "question" : "questions"}
            {s.immediate ? ` · ${s.immediate} immediate` : ""}
            {s.answered ? ` · ${s.answered} answered by the agent` : ""}
          </span>
        ) : null}
      </div>

      {s.noneCaptured ? (
        <p className="mt-2 text-[14px] text-ink">Patient has questions — none captured; call to ask.</p>
      ) : (
        <ol className="mt-3 space-y-2">
          {entries.map((e) => (
            <QuestionItem
              key={e.index}
              q={e.q}
              onReview={onReview ? (body) => onReview(e.index, body) : undefined}
              timeZone={timeZone}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function QuestionItem({
  q,
  onReview,
  timeZone,
}: {
  q: PatientQuestion;
  onReview?: (body: { verdict: ShadowVerdict; note?: string }) => Promise<void>;
  timeZone?: string;
}) {
  const handling = questionHandling(q);
  return (
    <li
      className={cn(
        "rounded-xl px-3 py-2.5 ring-1 ring-inset",
        handling === "unsatisfied" ? "bg-brand-yellow/20 ring-brand-yellow/50" : "bg-white/80 ring-black/[0.05]",
      )}
    >
      <p className="text-[14px] leading-snug text-ink">&ldquo;{q.text}&rdquo;</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {handling !== "answered" ? <Pill tone={PRIORITY_TONE[q.priority]}>{PRIORITY_LABEL[q.priority]}</Pill> : null}
        {handling !== "nurse" ? <Pill tone="good" icon={BookOpenCheck}>Answered by the agent</Pill> : null}
        {handling === "unsatisfied" ? (
          <Pill tone="routine" icon={ThumbsDown}>Patient said this didn&rsquo;t answer it</Pill>
        ) : null}
        <Pill tone="muted">{categoryLabel(q.category)}</Pill>
      </div>
      {handling !== "nurse" ? (
        <div className="mt-2 rounded-lg bg-mist/60 px-3 py-2 ring-1 ring-inset ring-black/[0.04]">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
            {q.sources?.length ? "The agent said" : <>The agent said · {answerSource(q)}</>}
          </p>
          <p className="mt-0.5 text-[13px] leading-snug text-ink">
            {q.answer ? <>&ldquo;{q.answer}&rdquo;</> : <span className="text-ink-muted">The answer was not recorded.</span>}
          </p>
          {q.sources?.length || typeof q.support_score === "number" ? (
            <div className="mt-2 space-y-1.5">
              <SourceChips sources={q.sources} />
              <SupportScore value={q.support_score} />
            </div>
          ) : null}
        </div>
      ) : null}
      {q.shadow ? <ShadowBlock draft={q.shadow} review={q.shadow_review} onReview={onReview} timeZone={timeZone} /> : null}
    </li>
  );
}

/**
 * Shadow mode: what the agent would have said, kept visibly apart from what
 * the patient heard, with the nurse's rating.
 */
function ShadowBlock({
  draft,
  review,
  onReview,
  timeZone,
}: {
  draft: ShadowDraft;
  review?: ShadowReview;
  onReview?: (body: { verdict: ShadowVerdict; note?: string }) => Promise<void>;
  timeZone?: string;
}) {
  const noteId = useId();
  const [editing, setEditing] = useState(false);
  const [verdict, setVerdict] = useState<ShadowVerdict | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const rating = !review || editing;
  const answered = draft.outcome === "answered" && !!draft.answer;

  const start = () => {
    setVerdict(review?.verdict ?? null);
    setNote(review?.note ?? "");
    setEditing(true);
  };
  const save = async () => {
    if (!onReview || !verdict || busy || note.length > MAX_REVIEW_NOTE) return;
    setBusy(true);
    try {
      const n = note.trim();
      await onReview(n ? { verdict, note: n } : { verdict });
      setEditing(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the rating.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      aria-label="Agent's draft answer, not said to the patient"
      className="mt-2 rounded-lg border border-dashed border-brand-blue/40 bg-brand-blue/[0.05] px-3 py-2"
    >
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand-blue-dark">
        <Sparkles className="h-3.5 w-3.5" /> Agent&rsquo;s draft answer — not said to the patient
      </p>
      <p className="mt-1 text-[13px] leading-snug text-ink">
        {answered ? (
          <>&ldquo;{draft.answer}&rdquo;</>
        ) : (
          <span className="text-ink-soft">The agent would have deferred: {shortReason(draft.reason)}.</span>
        )}
      </p>
      {draft.sources?.length || typeof draft.support_score === "number" ? (
        <div className="mt-2 space-y-1.5">
          <SourceChips sources={draft.sources} />
          <SupportScore value={draft.support_score} />
        </div>
      ) : null}

      <div className="mt-2 border-t border-brand-blue/15 pt-2">
        {!rating && review ? (
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={review.verdict === "good" ? "good" : "urgent"} icon={review.verdict === "good" ? ThumbsUp : ThumbsDown}>
              {VERDICT_LABEL[review.verdict]}
            </Pill>
            <span className="text-[12px] text-ink-soft">
              Rated by {review.by || "you"}
              {review.at ? `, ${fmtDateTime(review.at, { month: "short", day: "numeric", timeZone })}` : ""}
            </span>
            {onReview ? (
              <button type="button" onClick={start} className="ml-auto text-[12px] font-semibold text-brand-deep hover:underline">
                Change
              </button>
            ) : null}
            {review.note ? <p className="w-full text-[12px] text-ink-soft">&ldquo;{review.note}&rdquo;</p> : null}
          </div>
        ) : onReview ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12px] font-semibold text-ink">Was this draft right?</span>
              {(["good", "not_right"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={verdict === v}
                  disabled={busy}
                  onClick={() => setVerdict(v)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12px] font-semibold ring-1 ring-inset disabled:opacity-50",
                    verdict === v
                      ? v === "good" ? "bg-brand-deep text-white ring-brand-deep" : "bg-brand-coral text-white ring-brand-coral"
                      : "bg-white text-ink-soft ring-black/[0.08] hover:text-ink",
                  )}
                >
                  {v === "good" ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}
                  {VERDICT_LABEL[v]}
                </button>
              ))}
            </div>
            {verdict ? (
              <>
                <label className="sr-only" htmlFor={noteId}>Note (optional)</label>
                <textarea
                  id={noteId}
                  value={note}
                  rows={2}
                  maxLength={MAX_REVIEW_NOTE}
                  disabled={busy}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={verdict === "not_right" ? "What was wrong? (optional, no patient details)" : "Note (optional, no patient details)"}
                  className="w-full rounded-lg border border-black/[0.08] bg-white px-2.5 py-1.5 text-[13px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                />
                <div className="flex items-center gap-2">
                  <span className="text-[11px] tabular-nums text-ink-muted">{note.length} / {MAX_REVIEW_NOTE}</span>
                  {review ? (
                    <button type="button" disabled={busy} onClick={() => setEditing(false)}
                      className="ml-auto text-[12px] font-semibold text-ink-soft hover:text-ink">
                      Cancel
                    </button>
                  ) : null}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={save}
                    className={cn("rounded-full bg-brand-deep px-3 py-1 text-[12px] font-semibold text-white disabled:opacity-50", !review && "ml-auto")}
                  >
                    {busy ? "Saving…" : "Save rating"}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <p className="text-[12px] text-ink-muted">Not rated yet.</p>
        )}
      </div>
    </div>
  );
}

/** The small "N questions" pill for call lists. */
export function QuestionCountPill({ call, pill }: { call?: CallDoc; pill?: QuestionPill | null }) {
  const p = pill ?? (call ? questionPill(call) : null);
  if (!p) return null;
  return (
    <span title={p.title}>
      <Pill tone={p.tone} icon={MessageCircleQuestion}>{p.label}</Pill>
    </span>
  );
}
