"use client";

import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, CircleAlert } from "lucide-react";
import type { EhrProblem } from "@/lib/ehr-export";
import { cn } from "@/lib/utils";
import { Panel } from "@/components/nurse/parts";

/** Small form parts shared by the EHR export screens (native controls: work on phones). */

export function Card({ title, icon, actions, children, className, muted }: { title: ReactNode; icon?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; muted?: boolean }) {
  return (
    <Panel className={cn("min-w-0 p-4 sm:p-5", muted && "opacity-90", className)}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {icon}
        <h3 className="min-w-0 font-display text-[15px] font-semibold text-ink">{title}</h3>
        {actions ? <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </Panel>
  );
}

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block min-w-0 text-[12px]", className)}>
      <span className="mb-1 block font-semibold text-ink-soft">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-[11.5px] text-ink-muted">{hint}</span> : null}
    </label>
  );
}

const control = "h-8 w-full min-w-0 rounded-lg border border-rule bg-white px-2 text-[13px] text-ink shadow-sm focus:outline-none focus:ring-2 focus:ring-brand/40 disabled:bg-mist disabled:text-ink-muted";

/**
 * A text input that keeps its own value while typing and reports each change.
 * (Controlled by the profile; re-synced when the profile changes from outside.)
 */
export function TextInput({ value, onChange, className, invalid, ...rest }: { value: string; onChange: (v: string) => void; invalid?: boolean } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input
      {...rest}
      value={v}
      onChange={(e) => {
        setV(e.target.value);
        onChange(e.target.value);
      }}
      className={cn(control, invalid && "border-brand-red/60 ring-1 ring-brand-red/30", className)}
    />
  );
}

export function Select<T extends string>({ value, options, onChange, className, disabled, "aria-label": ariaLabel }: { value: T | ""; options: { value: T; label: string; disabled?: boolean }[]; onChange: (v: T) => void; className?: string; disabled?: boolean; "aria-label"?: string }) {
  return (
    <select aria-label={ariaLabel} disabled={disabled} value={value} onChange={(e) => onChange(e.target.value as T)} className={cn(control, "pr-6", className)}>
      {value === "" ? <option value="">Choose…</option> : null}
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Toggle({ checked, onChange, label, disabled, title }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean; title?: string }) {
  return (
    <label className={cn("inline-flex cursor-pointer select-none items-center gap-2 text-[13px] font-semibold text-ink", disabled && "cursor-not-allowed opacity-60")} title={title}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors", checked ? "bg-brand" : "bg-rule")}
      >
        <span className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all", checked ? "left-[18px]" : "left-0.5")} />
      </button>
      {label}
    </label>
  );
}

export function Check({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean }) {
  return (
    <label className={cn("flex cursor-pointer items-start gap-2 text-[13px] text-ink", disabled && "cursor-not-allowed opacity-60")}>
      <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-brand)]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0">{label}</span>
    </label>
  );
}

export function ProblemLines({ problems, compact }: { problems: EhrProblem[]; compact?: boolean }) {
  if (!problems.length) return null;
  return (
    <ul className={cn("space-y-1", compact ? "text-[11.5px]" : "text-[12.5px]")}>
      {problems.map((p, i) => (
        <li key={i} className={cn("flex min-w-0 items-start gap-1.5", p.level === "error" ? "text-brand-red-dark" : "text-ink-soft")}>
          {p.level === "error" ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-orange" />}
          <span className="min-w-0 break-words">{p.message}</span>
        </li>
      ))}
    </ul>
  );
}

export function Note({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-xl px-3 py-2 text-[12.5px] ring-1",
        tone === "warn" && "bg-brand-orange/15 text-ink ring-brand-orange/40",
        tone === "error" && "bg-brand-red/10 text-brand-red-dark ring-brand-red/30",
        tone === "info" && "bg-brand-blue/8 text-ink-soft ring-brand-blue/20",
      )}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function TabBar<T extends string>({ tabs, value, onChange, label }: { tabs: { value: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={t.value === value}
          onClick={() => onChange(t.value)}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-all",
            t.value === value ? "bg-brand-deep text-white shadow-[0_6px_16px_-6px_rgba(29,77,82,0.6)]" : "bg-white/70 text-ink-soft ring-1 ring-black/[0.06] hover:bg-white hover:text-ink",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** A value that settles `ms` after its last change. */
export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
