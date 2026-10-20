"use client";

import { MessageSquareText } from "lucide-react";
import { cn } from "@/lib/utils";
import { smsIndicator, SmsVariant } from "@/lib/sms-control";
import { useTimeFormat } from "@/lib/time";
import { SmsControlMode, useSmsControl } from "@/hooks/use-sms-control";

const VARIANT: Record<SmsVariant, string> = {
  // Filled: this dashboard's system receives the texts.
  self: "bg-brand-deep text-white ring-brand-deep shadow-sm",
  // Outlined: the other system does.
  peer: "bg-white/70 text-brand-deep ring-brand-deep/60",
  // Neither URL matches, or the number could not be read.
  unknown: "bg-amber-100 text-amber-900 ring-amber-400",
};

/**
 * Top-right pill: "ADK active" / "GE-CX active" / "SMS: Unknown"
 * (docs/sms-number-switch.md). Hidden when the switch is off. With
 * `onClick` it is a button (the admin console opens Messaging); with `href`
 * a link (the nurse console links to the admin console, where the switch
 * lives); otherwise display only.
 */
export function SmsIndicator({
  mode = "live",
  onClick,
  href,
  className,
}: {
  mode?: SmsControlMode;
  onClick?: () => void;
  href?: string | null;
  className?: string;
}) {
  useTimeFormat();
  const { status, error } = useSmsControl(mode);
  const ind = smsIndicator(status, error);
  if (!ind) return null;

  const classes = cn(
    "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full font-semibold leading-none ring-1 ring-inset",
    "px-2.5 py-1.5 text-[11px] sm:px-3.5 sm:py-2 sm:text-[12px]",
    VARIANT[ind.variant],
    className,
  );
  const content = (
    <>
      <MessageSquareText className="h-3.5 w-3.5" aria-hidden />
      {ind.label}
    </>
  );
  if (href) {
    return (
      <a href={href} title={`${ind.tooltip} Change it in the admin console.`} aria-label={`${ind.label}. ${ind.tooltip} Opens the admin console.`}
         className={cn(classes, "transition-opacity hover:opacity-85")}>
        {content}
      </a>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={ind.tooltip} aria-label={`${ind.label}. ${ind.tooltip}`}
              className={cn(classes, "transition-opacity hover:opacity-85")}>
        {content}
      </button>
    );
  }
  return (
    <span role="status" title={ind.tooltip} aria-label={`${ind.label}. ${ind.tooltip}`} className={classes}>
      {content}
    </span>
  );
}
