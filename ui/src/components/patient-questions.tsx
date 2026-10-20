"use client";

import { BookOpenCheck, MessageCircleQuestion, ThumbsDown } from "lucide-react";
import { CallDoc, PatientQuestion } from "@/lib/types";
import {
  PRIORITY_LABEL,
  PRIORITY_TONE,
  QuestionPill,
  answerSource,
  categoryLabel,
  questionHandling,
  questionPill,
  questionSummary,
} from "@/lib/patient-questions";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/nurse/parts";

/**
 * "Patient questions — nurse follow-up required": each question in the
 * patient's own words, immediate first, with category and priority chips.
 * Renders nothing when the patient said no, or the call predates the
 * question (the findings grid already says "None" / "Not asked").
 *
 * With the knowledge base on (docs/KNOWLEDGE-PLAN.md), a question the agent
 * answered from approved clinic Q&A shows the answer as spoken and its
 * source, so a nurse can correct it. Questions without the new fields look
 * exactly as before.
 */
export function PatientQuestionsSection({ call, className }: { call: CallDoc; className?: string }) {
  const s = questionSummary(call);
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
          {s.questions.map((q, i) => <QuestionItem key={i} q={q} />)}
        </ol>
      )}
    </section>
  );
}

function QuestionItem({ q }: { q: PatientQuestion }) {
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
            The agent said · {answerSource(q)}
          </p>
          <p className="mt-0.5 text-[13px] leading-snug text-ink">
            {q.answer ? <>&ldquo;{q.answer}&rdquo;</> : <span className="text-ink-muted">The answer was not recorded.</span>}
          </p>
        </div>
      ) : null}
    </li>
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
