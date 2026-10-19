"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  CalendarDays,
  ClipboardCheck,
  FlaskConical,
  LayoutGrid,
  Search,
  Settings,
  Siren,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { LOGO_WIDE_URL, ORG_NAME } from "@/lib/site";
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
import { ScheduleView } from "./schedule-view";
import { CompletedPreset, CompletedView, KIND } from "./completed-view";
import { TIER } from "./follow-up-list";
import { SettingsView } from "./settings-view";
import { DrawerSubject, PatientDrawer } from "./patient-drawer";

export type View = "schedule" | "completed" | "settings";

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
}: {
  data: NurseData;
  mode: "demo" | "live";
  loading?: boolean;
  actions: NurseActions;
  /** A call-list run is in progress. */
  running?: boolean;
  /** The proxy's outbound allowlist (live only). */
  allowlist?: AllowlistResponse | null;
  /** Console settings (auto-recall). */
  settings?: CallSettingsResponse | null;
  /** Live sources that failed to load: shown, never read as "nothing to do". */
  unavailable?: string[];
}) {
  const [view, setView] = useState<View>("schedule");
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState<DrawerSubject | null>(null);
  // Call status opened at a filter, from a KPI tile or the emergency banner.
  // (The separate Follow-ups tab was removed on 19 Oct: it repeated Call
  // status's follow-up filters.)
  const [preset, setPreset] = useState<CompletedPreset | null>(null);
  const showCompleted = (filter: CompletedFilter, scope: DayScope = "all") => {
    setPreset({ filter, scope, n: Date.now() });
    setView("completed");
  };
  const now = useNow();

  const all = useMemo(
    () => ({
      schedule: scheduleItems(data.entries, data.runs, data.callbacks, data.sessions, data.calls, now),
      completed: completedItems(data.calls, data.sessions, data.callbacks, data.entries),
      followups: followUpItems(data.calls, data.callbacks, now),
    }),
    [data, now],
  );

  const q = query.trim().toLowerCase();
  const hit = (name: string, id?: string) => !q || name.toLowerCase().includes(q) || (id ?? "").toLowerCase().includes(q);
  const schedule = all.schedule.filter((i) => hit(i.patientName, i.patientId));
  const completed = all.completed.filter((i) => hit(i.patientName, i.patientId));
  const entries = data.entries.filter((e) => hit(e.patient_name, e.patient_id));

  const openFollowUps = all.followups;
  const emergencies = openFollowUps.filter((i) => i.tier === "emergency");
  const overdueCallIds = useMemo(
    () => new Set(openFollowUps.filter((i) => i.overdue).map((i) => i.call.doc_id)), [openFollowUps]);
  const counts: Record<View, number> = {
    schedule: all.schedule.length,
    completed: all.completed.filter((i) => isSameDay(i.at, now)).length,
    settings: data.entries.length,
  };

  const openSchedule = (i: ScheduleItem) =>
    setDrawer({
      name: i.patientName, patientId: i.patientId, phone: i.phone, procedure: i.procedure, tone: "info",
      scheduledFor: i.when,
      call: data.calls.find((c) => (c.call_sid ?? c.doc_id) === i.sourceCallSid),
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
    <div className="relative min-h-[calc(100vh-37px)] overflow-hidden bg-[#f4f6f5] font-sans text-ink">
      <Backdrop />
      <div className="relative mx-auto flex max-w-[1500px] gap-6 p-4 lg:p-6">
        <Sidebar view={view} onView={setView} counts={counts} mode={mode} emergencies={emergencies.length} />

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

          <Header now={now} query={query} onQuery={setQuery} />

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

          {view !== "settings" ? (
            <Kpis
              now={now}
              schedule={all.schedule}
              completed={all.completed}
              followups={openFollowUps}
              onView={setView}
              onFilter={showCompleted}
            />
          ) : null}

          <div>
            <div className="mb-4 flex items-end justify-between gap-4 px-1">
              <div>
                <h2 className="font-display text-2xl font-semibold tracking-tight text-ink">
                  {current?.label ?? "Settings"}
                </h2>
                <p className="mt-0.5 text-[13px] text-ink-muted">
                  {current?.blurb ?? "Demo administration: the patients the agent will call, and when."}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {running ? <Pill tone="good" pulse>Calls in progress</Pill> : null}
                {loading ? <Pill tone="muted" pulse>Loading</Pill> : null}
              </div>
            </div>

            {view === "schedule" ? (
              <ScheduleView items={schedule} runs={data.runs} now={now} actions={actions} running={running} allowlist={allowlist} onOpen={openSchedule} />
            ) : null}
            {view === "completed" ? (
              <CompletedView items={completed} now={now} onOpen={openCompleted}
                             overdueCallIds={overdueCallIds} preset={preset} />
            ) : null}
            {view === "settings" ? <SettingsView entries={entries} runs={data.runs} actions={actions} now={now} allowlist={allowlist} settings={settings} /> : null}
          </div>
        </main>
      </div>

      <MobileNav view={view} onView={setView} counts={counts} />
      <PatientDrawer
        subject={drawer}
        now={now}
        actions={actions}
        runs={data.runs}
        settings={settings}
        onClose={() => setDrawer(null)}
        onCallUpdated={() => actions.refresh()}
      />
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

function Backdrop() {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      <div className="absolute -left-32 -top-40 h-[34rem] w-[34rem] rounded-full bg-brand-mint/35 blur-[120px]" />
      <div className="absolute right-[-10rem] top-24 h-[28rem] w-[28rem] rounded-full bg-brand-turquoise/25 blur-[120px]" />
      <div className="absolute bottom-[-12rem] left-1/3 h-[30rem] w-[30rem] rounded-full bg-brand-yellow/15 blur-[120px]" />
      <div className="absolute inset-0 bg-[radial-gradient(rgba(29,77,82,0.06)_1px,transparent_1px)] [background-size:22px_22px]" />
    </div>
  );
}

function NavButton({
  active,
  onClick,
  icon: Icon,
  label,
  count,
  alert,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  count: number;
  alert?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-[14px] font-semibold transition-all",
        active ? "bg-white text-brand-deep shadow-lg" : "text-white/75 hover:bg-white/10 hover:text-white",
      )}
    >
      <Icon className="h-[18px] w-[18px]" />
      {label}
      <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[11px] tabular-nums",
        active ? "bg-brand/10 text-brand-dark" : "bg-white/10 text-white/80")}>
        {count}
      </span>
      {alert ? (
        <span className="relative -ml-1 flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-brand-red opacity-75 motion-safe:animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-red" />
        </span>
      ) : null}
    </button>
  );
}

function Sidebar({
  view,
  onView,
  counts,
  mode,
  emergencies,
}: {
  view: View;
  onView: (v: View) => void;
  counts: Record<View, number>;
  mode: "demo" | "live";
  emergencies: number;
}) {
  return (
    <aside className="sticky top-6 hidden h-[calc(100vh-37px-3rem)] w-[17rem] shrink-0 flex-col overflow-hidden rounded-[32px] bg-gradient-to-b from-brand-deep to-brand-deep-dark p-5 text-white shadow-[0_24px_60px_-24px_rgba(22,59,63,0.7)] lg:flex">
      <div className="pointer-events-none absolute -left-16 -top-16 h-56 w-56 rounded-full bg-brand-bright/25 blur-3xl" />
      {LOGO_WIDE_URL ? (
        <div className="relative">
          <div className="rounded-2xl bg-white px-4 py-3 shadow-lg">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={LOGO_WIDE_URL} alt={ORG_NAME} className="mx-auto h-11 w-auto" />
          </div>
          <div className="mt-3 px-1 text-[12px] font-semibold uppercase tracking-[0.14em] text-white/60">
            Post-op nurse console
          </div>
        </div>
      ) : (
        <div className="relative flex items-center gap-3 px-1">
          <BrandTile className="h-12 w-12" />
          <div className="min-w-0">
            <div className="truncate font-display text-[15px] font-semibold leading-tight">{ORG_NAME}</div>
            <div className="text-[12px] text-white/60">Post-op nurse console</div>
          </div>
        </div>
      )}

      <nav className="relative mt-8 space-y-1.5">
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
      </nav>

      <div className="relative mt-auto space-y-3">
        <NavButton active={view === "settings"} onClick={() => onView("settings")} icon={Settings} label="Settings" count={counts.settings} />
        <div className="rounded-2xl bg-white/[0.07] p-4 ring-1 ring-white/10">
          <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-white/60">
            <Activity className="h-3.5 w-3.5" /> Data
          </div>
          <div className="mt-2 flex rounded-full bg-black/20 p-1 text-[12px] font-semibold">
            <Link href="/nurse" className={cn("flex-1 rounded-full py-1.5 text-center transition-colors",
              mode === "live" ? "bg-white text-brand-deep" : "text-white/70 hover:text-white")}>Live</Link>
            <Link href="/nurse?data=demo" className={cn("flex-1 rounded-full py-1.5 text-center transition-colors",
              mode === "demo" ? "bg-white text-brand-deep" : "text-white/70 hover:text-white")}>Sample</Link>
          </div>
        </div>
        <Link href="/classic" className="flex items-center gap-2 rounded-2xl px-3.5 py-2.5 text-[13px] font-semibold text-white/70 transition-colors hover:bg-white/10 hover:text-white">
          <LayoutGrid className="h-4 w-4" /> Classic dashboard
        </Link>
      </div>
    </aside>
  );
}

function MobileNav({ view, onView, counts }: { view: View; onView: (v: View) => void; counts: Record<View, number> }) {
  const items = [...NAV, { view: "settings" as View, label: "Settings", blurb: "", icon: Settings }];
  return (
    <nav className="fixed inset-x-3 bottom-3 z-40 flex rounded-[24px] bg-brand-deep/95 p-1.5 shadow-2xl backdrop-blur-xl lg:hidden">
      {items.map(({ view: v, label, icon: Icon }) => (
        <button
          key={v}
          onClick={() => onView(v)}
          className={cn("flex flex-1 flex-col items-center gap-0.5 rounded-[18px] py-2 text-[11px] font-semibold",
            v === view ? "bg-white text-brand-deep" : "text-white/70")}
        >
          <Icon className="h-[18px] w-[18px]" />
          <span className="truncate">{v === "schedule" ? "Queue" : v === "completed" ? "Status" : label}</span>
          <span className="text-[10px] opacity-70">{counts[v]}</span>
        </button>
      ))}
    </nav>
  );
}

function Header({ now, query, onQuery }: { now: Date; query: string; onQuery: (q: string) => void }) {
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
      <label className="relative ml-auto w-full sm:w-80">
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
