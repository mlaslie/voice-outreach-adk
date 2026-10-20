"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { fmtClock } from "@/lib/time";
import { useBranding } from "@/components/branding-provider";
import { initials } from "@/lib/nurse/model";

// ---------------------------------------------------------------------------
// Tones — one small vocabulary for every badge, stripe and avatar.
// ---------------------------------------------------------------------------

export type Tone = "emergency" | "urgent" | "routine" | "good" | "info" | "teal" | "muted" | "warn";

export const TONE: Record<Tone, { pill: string; dot: string; stripe: string; avatar: string }> = {
  emergency: { pill: "bg-brand-red/10 text-brand-red-dark ring-brand-red/20", dot: "bg-brand-red", stripe: "bg-brand-red", avatar: "bg-brand-red/12 text-brand-red-dark" },
  urgent: { pill: "bg-brand-coral/12 text-brand-coral-dark ring-brand-coral/25", dot: "bg-brand-coral", stripe: "bg-brand-coral", avatar: "bg-brand-coral/15 text-brand-coral-dark" },
  routine: { pill: "bg-brand-yellow/25 text-ink ring-brand-yellow/40", dot: "bg-brand-yellow", stripe: "bg-brand-yellow", avatar: "bg-brand-yellow/30 text-ink" },
  good: { pill: "bg-brand/10 text-brand-dark ring-brand/20", dot: "bg-brand", stripe: "bg-brand", avatar: "bg-brand/12 text-brand-dark" },
  info: { pill: "bg-brand-blue/10 text-brand-blue-dark ring-brand-blue/20", dot: "bg-brand-blue", stripe: "bg-brand-blue", avatar: "bg-brand-blue/12 text-brand-blue-dark" },
  teal: { pill: "bg-brand-turquoise/20 text-brand-deep ring-brand-turquoise/35", dot: "bg-brand-turquoise", stripe: "bg-brand-turquoise", avatar: "bg-brand-turquoise/20 text-brand-deep" },
  warn: { pill: "bg-brand-orange/20 text-ink ring-brand-orange/35", dot: "bg-brand-orange", stripe: "bg-brand-orange", avatar: "bg-brand-orange/20 text-ink" },
  muted: { pill: "bg-mist text-ink-soft ring-rule", dot: "bg-ink-muted", stripe: "bg-rule", avatar: "bg-mist-cool text-ink-soft" },
};

export function Pill({
  tone = "muted",
  icon: Icon,
  children,
  pulse,
  className,
}: {
  tone?: Tone;
  icon?: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold leading-none ring-1 ring-inset",
        TONE[tone].pill,
        className,
      )}
    >
      {pulse ? (
        <span className="relative flex h-1.5 w-1.5">
          <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", TONE[tone].dot)} />
          <span className={cn("relative inline-flex h-1.5 w-1.5 rounded-full", TONE[tone].dot)} />
        </span>
      ) : Icon ? (
        <Icon className="h-3.5 w-3.5" />
      ) : null}
      {children}
    </span>
  );
}

export function Avatar({ name, tone = "muted", size = "md", className }: { name: string; tone?: Tone; size?: "md" | "lg"; className?: string }) {
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center rounded-2xl font-display font-semibold",
        size === "lg" ? "h-14 w-14 text-lg" : "h-11 w-11 text-sm",
        TONE[tone].avatar,
        className,
      )}
      aria-hidden
    >
      {initials(name)}
    </span>
  );
}

export function Panel({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-[28px] bg-white/75 shadow-[0_1px_0_rgba(255,255,255,0.8)_inset,0_12px_40px_-12px_rgba(22,59,63,0.18)] ring-1 ring-black/[0.04] backdrop-blur-xl",
        className,
      )}
    >
      {children}
    </div>
  );
}

export interface ChipOption<T extends string> {
  value: T;
  label: string;
  count: number;
  tone?: Tone;
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ChipOption<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "group inline-flex shrink-0 items-center gap-2 rounded-full px-3.5 py-2 text-[13px] font-semibold transition-all",
              active
                ? "bg-brand-deep text-white shadow-[0_6px_16px_-6px_rgba(29,77,82,0.6)]"
                : "bg-white/70 text-ink-soft ring-1 ring-black/[0.06] hover:bg-white hover:text-ink",
            )}
          >
            {o.tone && !active ? <span className={cn("h-2 w-2 rounded-full", TONE[o.tone].dot)} /> : null}
            {o.label}
            <span
              className={cn(
                "min-w-[1.5rem] rounded-full px-1.5 py-0.5 text-center text-[11px] tabular-nums",
                active ? "bg-white/20 text-white" : "bg-mist text-ink-soft",
              )}
            >
              {o.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="grid place-items-center px-6 py-16 text-center">
      <div className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-brand/10 text-brand">
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <p className="font-display text-base font-semibold text-ink">{title}</p>
      {hint ? <p className="mt-1 max-w-sm text-sm text-ink-muted">{hint}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Brand mark
// ---------------------------------------------------------------------------

/**
 * The organisation's logo from Admin → Branding, else NEXT_PUBLIC_LOGO_URL
 * (config), else an original, generic pineapple placeholder. The placeholder
 * is deliberately NOT any organisation's registered mark.
 */
/** A square tile holding the logo: the real mark edge to edge, or the placeholder on white. */
export function BrandTile({ className }: { className?: string }) {
  const { logoUrl, staffOrgName } = useBranding();
  return (
    <div className={cn("grid shrink-0 place-items-center overflow-hidden rounded-2xl shadow-sm", logoUrl ? "" : "bg-white/95", className)}>
      {logoUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={logoUrl} alt={staffOrgName} className="h-full w-full object-cover" />
      ) : (
        <BrandMark className="h-[65%]" />
      )}
    </div>
  );
}

export function BrandMark({ className }: { className?: string }) {
  // Unique per instance: with shared ids, a copy inside a display:none
  // container (the desktop sidebar on a phone) breaks every other copy's
  // gradient references.
  const id = React.useId().replace(/:/g, "");
  const { logoUrl, staffOrgName } = useBranding();
  if (logoUrl) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={logoUrl} alt={staffOrgName} className={cn("h-10 w-auto", className)} />;
  }
  return (
    <svg viewBox="0 0 40 48" className={cn("h-10 w-auto", className)} role="img" aria-label="Logo placeholder">
      <defs>
        <linearGradient id={`pm-body-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffcd00" />
          <stop offset="1" stopColor="#ffaa47" />
        </linearGradient>
        <linearGradient id={`pm-leaf-${id}`} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#2ea84a" />
          <stop offset="1" stopColor="#7de69b" />
        </linearGradient>
        <clipPath id={`pm-clip-${id}`}>
          <ellipse cx="20" cy="32" rx="11" ry="14" />
        </clipPath>
      </defs>
      <path d="M20 19c-1.5-5-1-10 0-15 1 5 1.5 10 0 15Z" fill={`url(#pm-leaf-${id})`} />
      <path d="M19 19c-4-3-6.5-7-7.5-11.5 3.5 2.5 6 6 7.5 11.5Z" fill={`url(#pm-leaf-${id})`} />
      <path d="M21 19c4-3 6.5-7 7.5-11.5-3.5 2.5-6 6-7.5 11.5Z" fill={`url(#pm-leaf-${id})`} />
      <path d="M18.5 20c-4.5-1-8-3.5-10-7 4 .8 7.5 3 10 7Z" fill={`url(#pm-leaf-${id})`} opacity=".85" />
      <path d="M21.5 20c4.5-1 8-3.5 10-7-4 .8-7.5 3-10 7Z" fill={`url(#pm-leaf-${id})`} opacity=".85" />
      <ellipse cx="20" cy="32" rx="11" ry="14" fill={`url(#pm-body-${id})`} />
      <g clipPath={`url(#pm-clip-${id})`} stroke="#fff" strokeOpacity=".55" strokeWidth="1.2">
        {[-12, -4, 4, 12, 20].map((o) => (
          <line key={`a${o}`} x1={o} y1="18" x2={o + 28} y2="46" />
        ))}
        {[12, 20, 28, 36, 44].map((o) => (
          <line key={`b${o}`} x1={o} y1="18" x2={o - 28} y2="46" />
        ))}
      </g>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

/** "14:30" / "2:30 PM", per the clinic's time display setting. */
export function clock(iso: string, timeZone?: string): string {
  return fmtClock(iso, { timeZone });
}

export function ago(iso: string, now: Date): string {
  const s = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86_400);
  return d === 1 ? "yesterday" : `${d}d ago`;
}

/** "in 2h 10m" / "45m overdue". */
export function countdown(iso: string, now: Date): { text: string; late: boolean } {
  const ms = new Date(iso).getTime() - now.getTime();
  const late = ms < 0;
  const mins = Math.round(Math.abs(ms) / 60_000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const span = h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  return { text: late ? `${span} overdue` : `in ${span}`, late };
}

export function isSameDay(iso: string, now: Date): boolean {
  const d = new Date(iso);
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}
