"use client";

import Link from "next/link";
import { Activity, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useBranding } from "@/components/branding-provider";
import { BrandTile } from "@/components/nurse/parts";

/**
 * The frame both consoles share: backdrop, the dark sidebar with the brand
 * and a subtitle ("Post-op nurse console" / "Admin console"), its nav
 * buttons, the Live/Sample switch, and the phone's bottom nav. The nurse
 * console (/nurse) and the admin console (/admin) differ only in what they
 * put inside.
 */

export type NavIcon = React.ComponentType<{ className?: string }>;

export function Backdrop() {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      <div className="absolute -left-32 -top-40 h-[34rem] w-[34rem] rounded-full bg-brand-mint/35 blur-[120px]" />
      <div className="absolute right-[-10rem] top-24 h-[28rem] w-[28rem] rounded-full bg-brand-turquoise/25 blur-[120px]" />
      <div className="absolute bottom-[-12rem] left-1/3 h-[30rem] w-[30rem] rounded-full bg-brand-yellow/15 blur-[120px]" />
      <div className="absolute inset-0 bg-[radial-gradient(rgba(29,77,82,0.06)_1px,transparent_1px)] [background-size:22px_22px]" />
    </div>
  );
}

export function NavButton({
  active,
  onClick,
  icon: Icon,
  label,
  count,
  alert,
}: {
  active: boolean;
  onClick: () => void;
  icon: NavIcon;
  label: string;
  count?: number;
  alert?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-[14px] font-semibold transition-all",
        active ? "bg-white text-brand-deep shadow-lg" : "text-white/75 hover:bg-white/10 hover:text-white",
      )}
    >
      <Icon className="h-[18px] w-[18px]" />
      {label}
      {count !== undefined ? (
        <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[11px] tabular-nums",
          active ? "bg-brand/10 text-brand-dark" : "bg-white/10 text-white/80")}>
          {count}
        </span>
      ) : null}
      {alert ? (
        <span className="relative -ml-1 flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-brand-red opacity-75 motion-safe:animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-red" />
        </span>
      ) : null}
    </button>
  );
}

/** The sidebar: brand, `subtitle`, the nav (children), and `footer` above the data switch. */
export function SidebarShell({
  subtitle,
  basePath,
  mode,
  children,
  footer,
}: {
  subtitle: string;
  /** This console's page, for the Live / Sample switch ("/nurse", "/admin"). */
  basePath: string;
  mode: "demo" | "live";
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  // Admin → Branding when set, else the build-time NEXT_PUBLIC_* values (lib/site.ts).
  const { logoWideUrl, staffOrgName } = useBranding();
  return (
    <aside className="sticky top-6 hidden h-[calc(100vh-37px-3rem)] w-[17rem] shrink-0 flex-col overflow-hidden rounded-[32px] bg-gradient-to-b from-brand-deep to-brand-deep-dark p-5 text-white shadow-[0_24px_60px_-24px_rgba(22,59,63,0.7)] lg:flex">
      <div className="pointer-events-none absolute -left-16 -top-16 h-56 w-56 rounded-full bg-brand-bright/25 blur-3xl" />
      {logoWideUrl ? (
        <div className="relative">
          <div className="rounded-2xl bg-white px-4 py-3 shadow-lg">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={logoWideUrl} alt={staffOrgName} className="mx-auto h-11 max-w-full w-auto object-contain" />
          </div>
          <div className="mt-3 px-1 text-[12px] font-semibold uppercase tracking-[0.14em] text-white/60">
            {subtitle}
          </div>
        </div>
      ) : (
        <div className="relative flex items-center gap-3 px-1">
          <BrandTile className="h-12 w-12" />
          <div className="min-w-0">
            <div className="truncate font-display text-[15px] font-semibold leading-tight">{staffOrgName}</div>
            <div className="text-[12px] text-white/60">{subtitle}</div>
          </div>
        </div>
      )}

      <nav className="relative mt-8 space-y-1.5">{children}</nav>

      <div className="relative mt-auto space-y-3 pt-4">
        <div className="rounded-2xl bg-white/[0.07] p-4 ring-1 ring-white/10">
          <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-white/60">
            <Activity className="h-3.5 w-3.5" /> Data
          </div>
          <div className="mt-2 flex rounded-full bg-black/20 p-1 text-[12px] font-semibold">
            <Link href={basePath} className={cn("flex-1 rounded-full py-1.5 text-center transition-colors",
              mode === "live" ? "bg-white text-brand-deep" : "text-white/70 hover:text-white")}>Live</Link>
            <Link href={`${basePath}?data=demo`} className={cn("flex-1 rounded-full py-1.5 text-center transition-colors",
              mode === "demo" ? "bg-white text-brand-deep" : "text-white/70 hover:text-white")}>Sample</Link>
          </div>
        </div>
        {footer}
      </div>
    </aside>
  );
}

/** A sidebar footer link to a page outside this console (the other console). */
export function SidebarLink({
  href,
  icon: Icon,
  children,
  external,
}: {
  href: string;
  icon: NavIcon;
  children: React.ReactNode;
  /** Another service: a plain link, not client-side routing. */
  external?: boolean;
}) {
  const className = "flex items-center gap-2 rounded-2xl px-3.5 py-2.5 text-[13px] font-semibold text-white/70 transition-colors hover:bg-white/10 hover:text-white";
  if (external) {
    return (
      <a href={href} className={className}>
        <Icon className="h-4 w-4" /> {children}
        <ArrowUpRight className="ml-auto h-3.5 w-3.5 opacity-60" aria-hidden />
      </a>
    );
  }
  return (
    <Link href={href} className={className}>
      <Icon className="h-4 w-4" /> {children}
    </Link>
  );
}

export interface MobileNavItem<K extends string> {
  key: K;
  label: string;
  icon: NavIcon;
  count?: number;
}

/** The phone's bottom nav (the sidebar is hidden below lg). */
export function MobileNav<K extends string>({
  items,
  active,
  onSelect,
}: {
  items: MobileNavItem<K>[];
  active: K;
  onSelect: (k: K) => void;
}) {
  return (
    <nav className="fixed inset-x-3 bottom-3 z-40 flex rounded-[24px] bg-brand-deep/95 p-1.5 shadow-2xl backdrop-blur-xl lg:hidden">
      {items.map(({ key, label, icon: Icon, count }) => (
        <button
          key={key}
          onClick={() => onSelect(key)}
          aria-current={key === active ? "page" : undefined}
          className={cn("flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-[18px] py-2 text-[11px] font-semibold",
            key === active ? "bg-white text-brand-deep" : "text-white/70")}
        >
          <Icon className="h-[18px] w-[18px]" />
          <span className="max-w-full truncate px-1">{label}</span>
          {count !== undefined ? <span className="text-[10px] opacity-70">{count}</span> : null}
        </button>
      ))}
    </nav>
  );
}
