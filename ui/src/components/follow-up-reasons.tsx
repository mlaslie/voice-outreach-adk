"use client";

import { PenLine } from "lucide-react";
import type { CallDoc, FollowUpReason } from "@/lib/types";
import {
  agentOriginalReasons,
  nurseReasonsMeta,
  reasonTierLabel,
  reasonsFor,
  summariseReasons,
} from "@/lib/follow-up-reasons";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { Pill, Tone } from "./nurse/parts";

/** The console's tier colours, per reason. */
export const REASON_TONE: Record<string, Tone> = {
  emergency: "emergency",
  urgent_same_day: "urgent",
  routine_next_business_day: "routine",
  informational: "info",
  none: "good",
};

export function reasonTone(tier: string | undefined): Tone {
  return REASON_TONE[tier ?? ""] ?? "muted";
}

/** Each reason as a row: tier pill, then the text. Most urgent first (callers pass sorted lists). */
export function ReasonRows({
  reasons,
  variant = "panel",
  className,
}: {
  reasons: FollowUpReason[];
  variant?: "panel" | "band";
  className?: string;
}) {
  return (
    <ul className={cn(variant === "band" ? "flex flex-wrap gap-1.5" : "space-y-1.5", className)}>
      {reasons.map((r, i) => (
        <li
          key={`${r.tier}-${r.text}-${i}`}
          className={cn(
            "flex items-start gap-2",
            variant === "band" && "items-center rounded-full bg-white/90 py-0.5 pl-0.5 pr-3 text-ink shadow-sm",
          )}
        >
          <Pill tone={reasonTone(r.tier)} className="shrink-0">{reasonTierLabel(r.tier)}</Pill>
          <span className={cn("first-letter:uppercase", variant === "band" ? "text-[13px] font-medium" : "pt-0.5 text-[14px] leading-snug text-ink")}>
            {r.text}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Every reason on a call. When a nurse set them, says who and keeps the
 * agent's original assessment collapsed underneath. Renders nothing when
 * the call has no reasons at all (callers show their own fallback).
 */
export function FollowUpReasonList({
  call,
  variant = "panel",
  className,
}: {
  call: CallDoc;
  variant?: "panel" | "band";
  className?: string;
}) {
  const reasons = reasonsFor(call);
  if (!reasons.length) return null;
  const nurse = nurseReasonsMeta(call);
  const original = agentOriginalReasons(call);
  const band = variant === "band";
  return (
    <div className={cn("space-y-2", className)}>
      {nurse ? (
        <div className={cn("flex items-center gap-1.5 text-[12px] font-semibold", band ? "opacity-90" : "text-ink-soft")}>
          <PenLine className="h-3.5 w-3.5" />
          Set by {nurse.by}
          {nurse.at ? <span className="font-normal">· {fmtDateTime(nurse.at, { month: "short", day: "numeric" })}</span> : null}
        </div>
      ) : null}
      <ReasonRows reasons={reasons} variant={variant} />
      {nurse && original.length ? (
        <details className={cn("text-[12px]", band ? "opacity-90" : "text-ink-soft")}>
          <summary className="cursor-pointer font-semibold">Agent’s original assessment ({original.length})</summary>
          <ReasonRows reasons={original} variant={variant} className={cn("mt-2", band ? "" : "border-l-2 border-rule pl-3")} />
        </details>
      ) : null}
    </div>
  );
}

/**
 * Compact rows: the top reason plus "+N more", the rest in a tooltip.
 * Shows the tier pill only when asked (rows that already show the tier skip it).
 */
export function ReasonSummaryLine({
  call,
  showTier = false,
  className,
}: {
  call: CallDoc | null | undefined;
  showTier?: boolean;
  className?: string;
}) {
  const s = summariseReasons(reasonsFor(call));
  if (!s.top) return null;
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
      {showTier ? <Pill tone={reasonTone(s.top.tier)} className="shrink-0">{reasonTierLabel(s.top.tier)}</Pill> : null}
      <span className="truncate first-letter:uppercase">{s.top.text}</span>
      {s.rest.length ? (
        <span
          title={s.tooltip}
          aria-label={`${s.rest.length} more: ${s.tooltip.replace(/\n/g, "; ")}`}
          className="shrink-0 cursor-help rounded-full bg-mist px-1.5 py-0.5 text-[11px] font-semibold text-ink-soft ring-1 ring-inset ring-rule"
        >
          +{s.rest.length} more
        </span>
      ) : null}
    </span>
  );
}
