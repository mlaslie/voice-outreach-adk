"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTimeFormat } from "@/lib/time";
import {
  AlertTriangle,
  CalendarDays,
  ClipboardCheck,
  Clock,
  FlaskConical,
  PhoneOutgoing,
  Search,
  SlidersHorizontal,
  Siren,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { windowText } from "@/lib/nurse/window";
import { NurseData } from "@/lib/nurse/demo";
import { NurseActions } from "@/lib/nurse/actions";
import { AllowlistResponse, CallSettingsResponse } from "@/lib/types";
import {
  CompletedItem,
  FollowUpItem,
  ScheduleItem,
  completedItems,
  followUpItems,
  scheduleItems,
  CompletedFilter,
  DayScope,
} from "@/lib/nurse/model";
import { BrandTile, Panel, Pill, isSameDay } from "./parts";
import { Backdrop, MobileNav, NavButton, SidebarLink, SidebarShell } from "@/components/console-shell";
import { ScheduleView } from "./schedule-view";
import { CompletedPreset, CompletedView, KIND } from "./completed-view";
import { TIER } from "./follow-up-list";
import { SmsIndicator } from "@/components/sms-indicator";
import { DrawerSubject, PatientDrawer } from "./patient-drawer";
import { NewCallDialog } from "./new-call-dialog";

// Settings moved to the admin console (docs/ADMIN-PLAN.md step 1).
export type View = "schedule" | "completed";

const NAV: { view: View; label: string; blurb: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { view: "schedule", label: "Call queue", blurb: "Who is due a call and when. Call anyone now, or everyone.", icon: CalendarDays },
  { view: "completed", label: "Call status", blurb: "What happened on each call today, and what it needs from you.", icon: ClipboardCheck },
];

export function NurseConsole({
  data,
  mode,
  loading,
  actions,
  running = false,
  allowlist,
  settings,
  unavailable = [],
  adminConsoleUrl = null,
}: {
  data: NurseData;
  mode: "demo" | "live";
  loading?: boolean;
  actions: NurseActions;
  /** A call-list run is in progress. */
  running?: boolean;
  /** The proxy's outbound allowlist (live only). */
  allowlist?: AllowlistResponse | null;
  /** Console settings (calling hours, read-only here; the time format). */
  settings?: CallSettingsResponse | null;
  /** Live sources that failed to load: shown, never read as "nothing to do". */
  unavailable?: string[];
  /** The admin console, from the runtime env ADMIN_CONSOLE_URL; null hides the links. */
  adminConsoleUrl?: string | null;
}) {
  // Re-render the whole console when the clinic's 12h/24h setting changes.
  useTimeFormat();
  const [view, setView] = useState<View>("schedule");
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState<DrawerSubject | null>(null);
  const [newCallOpen, setNewCallOpen] = useState(false);
  // Call status opened at a filter, from a KPI tile or the emergency banner.
  // (The separate Follow-ups tab was removed on 19 Oct: it repeated Call
  // status's follow-up filters.)
  const [preset, setPreset] = useState<CompletedPreset | null>(null);
  const showCompleted = (filter: CompletedFilter, scope: DayScope = "all") => {
    setPreset({ filter, scope, n: Date.now() });
    setView("completed");
  };
  const now = useNow();

  // Calls deleted from Call status leave every list at once, before the
  // stream and the open-follow-ups poll catch up.
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const calls = useMemo(
    () => (deletedIds.size ? data.calls.filter((c) => !deletedIds.has(c.doc_id)) : data.calls),
    [data.calls, deletedIds],
  );
  const callById = useMemo(() => new Map(calls.map((c) => [c.doc_id, c])), [calls]);
  const onCallsRemoved = (ids: string[]) => {
    if (!ids.length) return;
    setDeletedIds((s) => new Set([...s, ...ids]));
    const gone = new Set(ids);
    setDrawer((d) => (d?.call && gone.has(d.call.doc_id) ? null : d));
  };

  const all = useMemo(
    () => ({
      schedule: scheduleItems(data.entries, data.runs, data.callbacks, data.sessions, calls, now),
      completed: completedItems(calls, data.sessions, data.callbacks, data.entries),
      followups: followUpItems(calls, data.callbacks, now),
    }),
    [data, calls, now],
  );

  const q = query.trim().toLowerCase();
  const hit = (name: string, id?: string) => !q || name.toLowerCase().includes(q) || (id ?? "").toLowerCase().includes(q);
  const schedule = all.schedule.filter((i) => hit(i.patientName, i.patientId));
  const completed = all.completed.filter((i) => hit(i.patientName, i.patientId));

  const openFollowUps = all.followups;
  const emergencies = openFollowUps.filter((i) => i.tier === "emergency");
  const overdueCallIds = useMemo(
    () => new Set(openFollowUps.filter((i) => i.overdue).map((i) => i.call.doc_id)), [openFollowUps]);
  const counts: Partial<Record<View, number>> = {
    schedule: all.schedule.length,
    completed: all.completed.filter((i) => isSameDay(i.at, now)).length,
  };

  const openSchedule = (i: ScheduleItem) =>
    setDrawer({
      name: i.patientName, patientId: i.patientId, phone: i.phone, procedure: i.procedure, tone: "info",
      scheduledFor: i.when,
      call: calls.find((c) => (c.call_sid ?? c.doc_id) === i.sourceCallSid),
      context: i.whenText ? <>Booked by text: “{i.whenText}”</> : undefined,
    });
  const openCompleted = (i: CompletedItem) =>
    setDrawer({
      name: i.patientName, patientId: i.patientId, phone: i.phone, procedure: i.procedure,
      tone: KIND[i.kind].tone, call: i.call,
      context: i.cancel ? `Cancelled — ${i.cancel.reason}` : KIND[i.kind].label,
      entryId: i.entry?.entry_id, cancel: i.cancel,
    });
  const openFollowUp = (i: FollowUpItem) =>
    setDrawer({
      name: i.patientName, patientId: i.patientId, phone: i.phone, procedure: i.procedure,
      tone: TIER[i.tier].tone, call: i.call, context: i.reason,
    });

  const current = NAV.find((n) => n.view === view);

  return (
    <div className="relative min-h-[calc(100vh-37px)] overflow-hidden bg-console font-sans text-ink">
      <Backdrop />
      <div className="relative mx-auto flex max-w-[1500px] gap-6 p-4 lg:p-6">
        <Sidebar view={view} onView={setView} counts={counts} mode={mode} emergencies={emergencies.length} adminConsoleUrl={adminConsoleUrl} />

        <main className="min-w-0 flex-1 space-y-6 pb-24 lg:pb-6">
          {unavailable.length ? (
            <div role="alert" className="flex items-center gap-3 rounded-2xl bg-brand-red/10 px-4 py-2.5 text-[13px] font-semibold text-brand-red-dark ring-1 ring-brand-red/30">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              Could not load {unavailable.join(" and ")}. Lists may be missing patients; nothing has been cleared.
              <button onClick={actions.refresh} className="ml-auto shrink-0 rounded-full bg-white px-3 py-1 text-[12px] text-ink ring-1 ring-black/[0.06]">
                Try again
              </button>
            </div>
          ) : null}
          {mode === "demo" ? (
            <div className="flex items-center gap-3 rounded-2xl bg-brand-yellow/30 px-4 py-2.5 text-[13px] font-semibold text-ink ring-1 ring-brand-yellow/60">
              <FlaskConical className="h-4 w-4 shrink-0" />
              Fictional sample data. Every patient here is invented and nothing you do is sent anywhere.
              <Link href="/nurse" className="ml-auto shrink-0 rounded-full bg-white px-3 py-1 text-[12px] text-brand-deep ring-1 ring-black/[0.06]">
                Switch to live
              </Link>
            </div>
          ) : null}

          <Header now={now} query={query} onQuery={setQuery} smsMode={mode} adminConsoleUrl={adminConsoleUrl} />

          {emergencies.length ? (
            <button
              onClick={() => { showCompleted("needs_immediate"); openFollowUp(emergencies[0]); }}
              className="group relative flex w-full items-center gap-4 overflow-hidden rounded-[24px] bg-gradient-to-r from-brand-red to-brand-coral px-5 py-4 text-left text-white shadow-[0_14px_40px_-14px_rgba(228,0,43,0.7)]"
            >
              <span className="absolute inset-0 bg-[radial-gradient(circle_at_10%_50%,rgba(255,255,255,0.25),transparent_45%)]" />
              <span className="relative grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-white/20">
                <Siren className="h-5 w-5 motion-safe:animate-pulse" />
              </span>
              <span className="relative min-w-0">
                <span className="block font-display text-[15px] font-semibold">
                  {emergencies.length === 1 ? "Emergency reported" : `${emergencies.length} emergencies reported`} — {emergencies[0].patientName}
                </span>
                <span className="block truncate text-[13px] text-white/85">{emergencies[0].reason}</span>
              </span>
              <span className="relative ml-auto hidden shrink-0 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-brand-red-dark sm:block">
                Review now
              </span>
            </button>
          ) : null}

          <Kpis
            now={now}
            schedule={all.schedule}
            completed={all.completed}
            followups={openFollowUps}
            onView={setView}
            onFilter={showCompleted}
          />

          <div>
            <div className="mb-4 flex items-end justify-between gap-4 px-1">
              <div>
                <h2 className="font-display text-2xl font-semibold tracking-tight text-ink">
                  {current?.label}
                </h2>
                <p className="mt-0.5 text-[13px] text-ink-muted">
                  {current?.blurb}
                </p>
                {view === "schedule" && settings?.recall ? (
                  // Read-only: calling hours are set in the admin console.
                  <p className="mt-1 flex items-center gap-1.5 text-[12px] font-semibold text-ink-soft">
                    <Clock className="h-3.5 w-3.5" aria-hidden />
                    Calls go out {windowText(settings.recall)} (clinic time)
                  </p>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {running ? <Pill tone="good" pulse>Calls in progress</Pill> : null}
                {loading ? <Pill tone="muted" pulse>Loading</Pill> : null}
                {view === "schedule" ? (
                  <button
                    type="button"
                    onClick={() => setNewCallOpen(true)}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm transition-transform hover:-translate-y-px"
                  >
                    <PhoneOutgoing className="h-3.5 w-3.5" /> New call
                  </button>
                ) : null}
              </div>
            </div>

            {view === "schedule" ? (
              <ScheduleView items={schedule} runs={data.runs} now={now} actions={actions} running={running} allowlist={allowlist} onOpen={openSchedule} />
            ) : null}
            {view === "completed" ? (
              <CompletedView items={completed} now={now} onOpen={openCompleted}
                             overdueCallIds={overdueCallIds} preset={preset}
                             actions={actions} latestCall={(id) => callById.get(id)} onRemoved={onCallsRemoved} />
            ) : null}
          </div>
        </main>
      </div>

      <MobileNav
        items={NAV.map(({ view: v, icon }) => ({ key: v, icon, label: v === "schedule" ? "Queue" : "Status", count: counts[v] }))}
        active={view}
        onSelect={setView}
      />
      <PatientDrawer
        subject={drawer}
        now={now}
        actions={actions}
        runs={data.runs}
        settings={settings}
        onClose={() => setDrawer(null)}
        onCallUpdated={() => actions.refresh()}
      />
      <NewCallDialog open={newCallOpen} onOpenChange={setNewCallOpen} actions={actions} allowlist={allowlist} />
    </div>
  );
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function Sidebar({
  view,
  onView,
  counts,
  mode,
  emergencies,
  adminConsoleUrl,
}: {
  view: View;
  onView: (v: View) => void;
  counts: Partial<Record<View, number>>;
  mode: "demo" | "live";
  emergencies: number;
  adminConsoleUrl: string | null;
}) {
  return (
    <SidebarShell
      subtitle="Post-op nurse console"
      basePath="/nurse"
      mode={mode}
      footer={adminConsoleUrl ? (
        <SidebarLink href={adminConsoleUrl} icon={SlidersHorizontal} external>Admin console</SidebarLink>
      ) : null}
    >
      {NAV.map(({ view: v, label, icon }) => (
        <NavButton
          key={v}
          active={v === view}
          onClick={() => onView(v)}
          icon={icon}
          label={label}
          count={counts[v]}
          alert={v === "completed" && emergencies > 0}
        />
      ))}
    </SidebarShell>
  );
}

function Header({
  now, query, onQuery, smsMode, adminConsoleUrl,
}: {
  now: Date; query: string; onQuery: (q: string) => void; smsMode: "demo" | "live"; adminConsoleUrl: string | null;
}) {
  const h = now.getHours();
  const greeting = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return (
    <header className="flex flex-wrap items-center gap-4">
      <div className="flex items-center gap-3 lg:hidden">
        <BrandTile className="h-11 w-11" />
      </div>
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-ink-muted">
          {now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
        </p>
        <h1 className="font-display text-[28px] font-semibold leading-tight tracking-tight text-ink">{greeting}</h1>
      </div>
      {/* Which system gets incoming texts; stays top-right on a phone, the search wraps below.
          Display only for nurses: the switch is in the admin console, linked when configured. */}
      <div className="flex-1" aria-hidden />
      <SmsIndicator mode={smsMode} href={adminConsoleUrl} />
      <label className="relative w-full sm:w-80">
        <Search className="pointer-events-none absolute left-4 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-ink-muted" />
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Find a patient or MRN"
          className="h-12 w-full rounded-full bg-white/80 pl-11 pr-4 text-[14px] text-ink shadow-sm ring-1 ring-black/[0.06] backdrop-blur placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-brand/40"
        />
      </label>
    </header>
  );
}

function Kpis({
  now,
  schedule,
  completed,
  followups,
  onView,
  onFilter,
}: {
  now: Date;
  schedule: ScheduleItem[];
  completed: CompletedItem[];
  followups: FollowUpItem[];
  onView: (v: View) => void;
  onFilter: (f: CompletedFilter) => void;
}) {
  const today = completed.filter((i) => isSameDay(i.at, now));
  const reached = today.filter((i) => i.kind === "completed_phone" || i.kind === "completed_text").length;
  const dueToday = schedule.filter((i) => i.state === "calling" || i.state === "overdue" || isSameDay(i.when, now)).length;
  const recalls = schedule.filter((i) => i.runId && i.origin === "retry" && isSameDay(i.when, now)).length;
  const rate = today.length ? Math.round((reached / today.length) * 100) : 0;
  const immediate = followups.filter((i) => i.tier !== "routine_next_business_day").length;
  const overdue = followups.filter((i) => i.overdue).length;

  const tiles = [
    { label: "Calls due today", value: dueToday, sub: recalls ? `${recalls} of them retries` : "still to come today", view: "schedule" as View, accent: "from-brand-blue/15" },
    { label: "Reached today", value: reached, sub: `${rate}% of ${today.length} ${today.length === 1 ? "attempt" : "attempts"}`, view: "completed" as View, accent: "from-brand/15", ring: rate },
    { label: "Immediate follow-up", value: immediate, sub: immediate ? "open now · emergency or same day" : "none open", filter: "needs_immediate" as CompletedFilter, accent: immediate ? "from-brand-coral/15" : "from-brand-mint/20", alert: immediate > 0 },
    { label: "Overdue", value: overdue, sub: overdue ? "past their callback window" : "nothing late", filter: "overdue" as CompletedFilter, accent: overdue ? "from-brand-red/15" : "from-brand-mint/20", alert: overdue > 0 },
  ];

  return (
    <div className="grid grid-cols-2 gap-3.5 xl:grid-cols-4">
      {tiles.map((t) => (
        <button key={t.label} onClick={() => ("filter" in t && t.filter ? onFilter(t.filter) : onView(t.view as View))}
                className="text-left">
          <Panel className={cn("relative h-full overflow-hidden p-5 transition-transform hover:-translate-y-0.5")}>
            <div className={cn("pointer-events-none absolute inset-0 bg-gradient-to-br to-transparent", t.accent)} />
            <div className="relative flex items-start justify-between">
              <div>
                <div className="text-[12px] font-semibold uppercase tracking-[0.1em] text-ink-muted">{t.label}</div>
                <div className={cn("mt-2 font-display text-[40px] font-semibold leading-none tabular-nums",
                  t.alert ? "text-brand-red-dark" : "text-ink")}>{t.value}</div>
                <div className="mt-2 text-[12px] text-ink-soft">{t.sub}</div>
              </div>
              {t.ring !== undefined ? <Ring pct={t.ring} /> : null}
            </div>
          </Panel>
        </button>
      ))}
    </div>
  );
}

function Ring({ pct }: { pct: number }) {
  const r = 22;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 56 56" className="h-14 w-14 -rotate-90">
      <circle cx="28" cy="28" r={r} fill="none" stroke="currentColor" strokeWidth="6" className="text-brand/10" />
      <circle cx="28" cy="28" r={r} fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} className="text-brand transition-all" />
    </svg>
  );
}
