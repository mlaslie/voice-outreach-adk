"use client";

import { MessageCircleQuestion } from "lucide-react";
import { CallDoc } from "@/lib/types";
import { PRIORITY_LABEL, PRIORITY_TONE, QuestionPill, categoryLabel, questionPill, questionSummary } from "@/lib/patient-questions";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/nurse/parts";

/**
 * "Patient questions — nurse follow-up required": each question in the
 * patient's own words, immediate first, with category and priority chips.
 * Renders nothing when the patient said no, or the call predates the
 * question (the findings grid already says "None" / "Not asked").
 */
export function PatientQuestionsSection({ call, className }: { call: CallDoc; className?: string }) {
  const s = questionSummary(call);
  if (!s.count && !s.noneCaptured) return null;
  const loud = s.immediate > 0;

  return (
    <section
      aria-label="Patient questions"
      className={cn(
        "rounded-2xl p-4 ring-1 ring-inset",
        loud ? "bg-brand-coral/[0.07] ring-brand-coral/30" : "bg-brand-yellow/15 ring-brand-yellow/40",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <MessageCircleQuestion className={cn("h-4 w-4", loud ? "text-brand-coral-dark" : "text-ink")} />
        <h4 className="text-[13px] font-semibold text-ink">Patient questions — nurse follow-up required</h4>
        {s.count ? (
          <span className="ml-auto text-[12px] text-ink-soft">
            {s.count} {s.count === 1 ? "question" : "questions"}
            {s.immediate ? ` · ${s.immediate} immediate` : ""}
          </span>
        ) : null}
      </div>

      {s.noneCaptured ? (
        <p className="mt-2 text-[14px] text-ink">Patient has questions — none captured; call to ask.</p>
      ) : (
        <ol className="mt-3 space-y-2">
          {s.questions.map((q, i) => (
            <li key={i} className="rounded-xl bg-white/80 px-3 py-2.5 ring-1 ring-inset ring-black/[0.05]">
              <p className="text-[14px] leading-snug text-ink">&ldquo;{q.text}&rdquo;</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Pill tone={PRIORITY_TONE[q.priority]}>{PRIORITY_LABEL[q.priority]}</Pill>
                <Pill tone="muted">{categoryLabel(q.category)}</Pill>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
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
