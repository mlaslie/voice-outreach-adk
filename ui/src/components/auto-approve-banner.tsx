"use client";

import { ShieldAlert } from "lucide-react";
import { AUTO_APPROVE_BANNER, LanguagesResponse, isAutoApproved } from "@/lib/languages";
import { cn } from "@/lib/utils";

/**
 * The demo "auto-approve all languages" warning. Nothing when the switch is
 * off (or the server is older and does not report it).
 */
export function AutoApproveBanner({
  resp,
  className,
}: {
  resp: LanguagesResponse | null | undefined;
  className?: string;
}) {
  if (!isAutoApproved(resp)) return null;
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-brand-orange bg-brand-orange/15 px-3 py-2 text-[13px] font-semibold text-ink",
        className,
      )}
    >
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-brand-orange" />
      <span>{AUTO_APPROVE_BANNER}</span>
    </div>
  );
}
