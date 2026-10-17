"use client";

import { AlertTriangle, Check, X } from "lucide-react";
import { CallDoc } from "@/lib/types";
import {
  FOLLOW_UP_SEVERITY,
  FollowUpTier,
  getFollowUpLabel,
  summarizeFollowUps,
} from "@/lib/status";

interface FollowUpBannerProps {
  calls: CallDoc[];
  /** True when the table is currently filtered to follow-up-needed calls. */
  active: boolean;
  onToggle: () => void;
}

/** Banner tone follows the most severe outstanding tier. */
const TONE: Record<FollowUpTier, string> = {
  emergency: "border-brand-red/50 bg-brand-red/10 text-brand-red-dark hover:bg-brand-red/15",
  urgent_same_day: "border-brand-orange bg-brand-orange/15 text-brand-coral-dark hover:bg-brand-orange/30",
  routine_next_business_day:
    "border-brand-yellow bg-brand-yellow/15 text-ink hover:bg-brand-yellow/40",
};

const DOT: Record<FollowUpTier, string> = {
  emergency: "bg-brand-red",
  urgent_same_day: "bg-brand-orange",
  routine_next_business_day: "bg-brand-yellow",
};

export function FollowUpBanner({ calls, active, onToggle }: FollowUpBannerProps) {
  const { total, byTier, worst } = summarizeFollowUps(calls);

  if (total === 0) {
    // Still worth showing when the filter is on, so the user has a way out
    // of an empty filtered table.
    if (!active) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-brand/40 bg-brand/10 px-4 py-2.5 text-sm text-brand-dark">
          <Check className="h-4 w-4 shrink-0" />
          No patients require follow-up.
        </div>
      );
    }
    return (
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 rounded-lg border border-rule bg-white px-4 py-2.5 text-sm text-ink hover:bg-mist"
      >
        <X className="h-4 w-4 shrink-0" />
        No patients require follow-up — clear filter
      </button>
    );
  }

  const tone = TONE[worst ?? "routine_next_business_day"];

  return (
    <button
      onClick={onToggle}
      aria-pressed={active}
      className={`flex w-full items-center justify-between gap-4 rounded-lg border px-4 py-3 text-left transition-colors ${tone} ${
        active ? "ring-2 ring-offset-1 ring-current/40" : ""
      }`}
    >
      <div className="flex items-center gap-3 min-w-0">
        <AlertTriangle className="h-5 w-5 shrink-0" />
        <div className="min-w-0">
          <p className="font-semibold text-sm">
            {total === 1 ? "1 patient requires" : `${total} patients require`} follow-up
          </p>
          <p className="text-xs opacity-80 flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
            {FOLLOW_UP_SEVERITY.filter((t) => byTier[t] > 0).map((t) => (
              <span key={t} className="inline-flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${DOT[t]}`} />
                {byTier[t]} {getFollowUpLabel(t).toLowerCase()}
              </span>
            ))}
          </p>
        </div>
      </div>

      <span className="shrink-0 text-xs font-medium underline underline-offset-2">
        {active ? "Clear filter" : "View these calls"}
      </span>
    </button>
  );
}
