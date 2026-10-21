"use client";

import { BookText, MessagesSquare } from "lucide-react";
import { KnowledgeSource } from "@/lib/types";
import { scoreText } from "@/lib/knowledge";
import { cn } from "@/lib/utils";

/**
 * The approved material an agent answer drew on, as chips (kind, title,
 * version), shared by Admin → Knowledge base "Try it" and the nurse drawer.
 */
export function SourceChips({ sources, className }: { sources?: KnowledgeSource[] | null; className?: string }) {
  if (!sources?.length) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="Sources">
      {sources.map((s, i) => {
        const Icon = s.kind === "guideline" ? BookText : MessagesSquare;
        return (
          <li
            key={`${s.kind}-${s.id}-${i}`}
            title={s.id}
            className="inline-flex max-w-full items-center gap-1 rounded-full bg-white px-2.5 py-1 text-[12px] text-ink-soft ring-1 ring-inset ring-black/[0.08]"
          >
            <Icon className="h-3.5 w-3.5 shrink-0 text-brand-deep" />
            <span className="shrink-0 font-semibold text-ink">{s.kind === "guideline" ? "Guideline" : "Example"}</span>
            <span className="truncate">{s.title || s.id}</span>
            {typeof s.version === "number" ? <span className="shrink-0 tabular-nums text-ink-muted">v{s.version}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** "Support 93%": how much of the answer the grounding check found backed by the sources. */
export function SupportScore({ value, min, className }: { value?: number | null; min?: number | null; className?: string }) {
  const v = scoreText(value);
  if (!v) return null;
  const low = typeof min === "number" && typeof value === "number" && value < min;
  return (
    <span
      className={cn("text-[12px] tabular-nums", low ? "font-semibold text-brand-coral-dark" : "text-ink-soft", className)}
      title="How much of the answer the grounding check found supported by the sources"
    >
      Support {v}
      {typeof min === "number" ? <span className="text-ink-muted"> (minimum {scoreText(min)})</span> : null}
    </span>
  );
}
