"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BookOpenCheck,
  CalendarClock,
  Clock,
  FlaskConical,
  Headphones,
  MessageSquareText,
  Palette,
  ScrollText,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
import { useTimeFormat } from "@/lib/time";
import { AdminActions, AdminReads } from "@/lib/nurse/actions";
import { AllowlistResponse, CallDoc, CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { FollowUpSession, ScheduledCallback, isOpen, upcoming } from "@/lib/followups";
import { BrandTile, Pill } from "@/components/nurse/parts";
import { Backdrop, MobileNav, NavButton, NavIcon, SidebarLink, SidebarShell } from "@/components/console-shell";
import { SmsIndicator } from "@/components/sms-indicator";
import { RecallSettingsPanel } from "@/components/nurse/recall-settings";
import { SmsNumberCard } from "@/components/nurse/sms-number-card";
import { RunsPanel } from "./runs-panel";
import { PatientsPanel } from "./patients-panel";
import { AllowlistPanel, ConsentMap } from "./allowlist-panel";
import { CompanyNamePanel, TextLimitPanel, TimeDisplayPanel } from "./settings-panels";
import { BookedCallbacksPanel, TextConversationsPanel } from "./messaging-panels";
import { TestByTextPanel, TestInBrowserPanel } from "./testing-panels";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { AuditPanel } from "./audit-panel";
import { BrandingPanel } from "./branding-panel";
import { KnowledgePanel } from "./knowledge-panel";

export type Section = "schedules" | "patients" | "numbers" | "messaging" | "knowledge" | "display" | "branding" | "testing" | "diagnostics" | "audit";

const SECTIONS: { key: Section; label: string; short: string; blurb: string; icon: NavIcon }[] = [
  { key: "schedules", label: "Schedules", short: "Runs", icon: CalendarClock,
    blurb: "Call runs, and the clinic's calling hours and auto-recall." },
  { key: "patients", label: "Patients", short: "Patients", icon: Users,
    blurb: "The patients the agent will call, with every detail it needs." },
  { key: "numbers", label: "Authorized numbers", short: "Numbers", icon: ShieldCheck,
    blurb: "The numbers the agent may dial, and which of them agreed to texts." },
  { key: "messaging", label: "Messaging", short: "Texts", icon: MessageSquareText,
    blurb: "The shared SMS number, the missed-call text limit, text conversations and booked callbacks." },
  { key: "knowledge", label: "Knowledge base", short: "Q&A", icon: BookOpenCheck,
    blurb: "Approved clinic answers the agent may read to patients word for word. Medication and symptom questions always go to a nurse." },
  { key: "display", label: "Display", short: "Display", icon: Clock,
    blurb: "The company name patients hear and read, and how times show for nurses." },
  { key: "branding", label: "Branding", short: "Brand", icon: Palette,
    blurb: "The staff consoles' title, organisation name, logos and colours. Patients never see these." },
  { key: "testing", label: "Testing", short: "Test", icon: Headphones,
    blurb: "Try the agent yourself with the fictional test patient: by voice in this browser, or by text. No phone line." },
  { key: "diagnostics", label: "Call diagnostics", short: "Diagnose", icon: Activity,
    blurb: "Millisecond timings, tool calls, interruptions and errors for one call. No answers or transcripts." },
  { key: "audit", label: "Audit log", short: "Audit", icon: ScrollText,
    blurb: "Every configuration change, with who made it and when." },
];

export interface AdminData {
  entries: CallListEntry[];
  runs: CallRun[];
  /** Text follow-up sessions and booked callbacks (Messaging). */
  sessions: FollowUpSession[];
  callbacks: ScheduledCallback[];
  /** Recent calls (Call diagnostics, and naming a conversation's call). */
  calls: CallDoc[];
}

/**
 * The admin console (/admin, the APP_MODE=admin build, service voa-admin):
 * configuration moved out of the nurse console's Settings. Same frame and
 * theme as the nurse console (components/console-shell.tsx).
 */
export function AdminConsole({
  data,
  mode,
  loading,
  actions,
  reads,
  allowlist,
  allowlistError,
  followUpsError,
  callsError,
  onRefreshCalls,
  consent,
  settings,
  unavailable = [],
  nurseConsoleUrl = null,
}: {
  data: AdminData;
  mode: "demo" | "live";
  loading?: boolean;
  actions: AdminActions;
  /** On-demand reads: a text thread, a call's timeline, the audit log. */
  reads: AdminReads;
  /** The last read of text sessions / callbacks failed. */
  followUpsError?: string | null;
  /** The last read of recent calls failed. */
  callsError?: string | null;
  onRefreshCalls?: () => void;
  allowlist: AllowlistResponse | null;
  allowlistError?: string | null;
  consent: ConsentMap;
  settings: CallSettingsResponse | null;
  /** Live sources that failed to load. */
  unavailable?: string[];
  /** The nurse console, from the runtime env NURSE_CONSOLE_URL; null hides the link. */
  nurseConsoleUrl?: string | null;
}) {
  useTimeFormat(); // re-render when the 12h/24h setting changes
  const [section, setSection] = useState<Section>("schedules");
  const [diagnosed, setDiagnosed] = useState<string | null>(null);
  const now = useNow();
  const current = SECTIONS.find((s) => s.key === section)!;
  const tz = settings?.timezone;

  const counts: Partial<Record<Section, number>> = {
    schedules: data.runs.filter((r) => r.status === "scheduled" || r.status === "dispatching").length,
    patients: data.entries.length,
    numbers: allowlist?.count,
    messaging: data.sessions.filter((s) => isOpen(s.state)).length + upcoming(data.callbacks).length,
  };

  const diagnose = (callId: string) => {
    setDiagnosed(callId);
    setSection("diagnostics");
  };

  return (
    <div className="relative min-h-[calc(100vh-37px)] overflow-hidden bg-console font-sans text-ink">
      <Backdrop />
      <div className="relative mx-auto flex max-w-[1500px] gap-6 p-4 lg:p-6">
        <SidebarShell
          subtitle="Admin console"
          basePath="/admin"
          mode={mode}
          footer={nurseConsoleUrl ? (
            <SidebarLink href={nurseConsoleUrl} icon={Stethoscope} external>Open nurse console</SidebarLink>
          ) : null}
        >
          {SECTIONS.map(({ key, label, icon }) => (
            <NavButton key={key} active={key === section} onClick={() => setSection(key)} icon={icon} label={label} count={counts[key]} />
          ))}
        </SidebarShell>

        <main className="min-w-0 flex-1 space-y-6 pb-24 lg:pb-6">
          {unavailable.length ? (
            <div role="alert" className="flex items-center gap-3 rounded-2xl bg-brand-red/10 px-4 py-2.5 text-[13px] font-semibold text-brand-red-dark ring-1 ring-brand-red/30">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              Could not load {unavailable.join(" and ")}. What shows may be out of date.
              <button onClick={actions.refresh} className="ml-auto shrink-0 rounded-full bg-white px-3 py-1 text-[12px] text-ink ring-1 ring-black/[0.06]">
                Try again
              </button>
            </div>
          ) : null}
          {mode === "demo" ? (
            <div className="flex items-center gap-3 rounded-2xl bg-brand-yellow/30 px-4 py-2.5 text-[13px] font-semibold text-ink ring-1 ring-brand-yellow/60">
              <FlaskConical className="h-4 w-4 shrink-0" />
              Fictional sample data. Every patient and number here is invented and nothing you do is sent anywhere.
              <Link href="/admin" className="ml-auto shrink-0 rounded-full bg-white px-3 py-1 text-[12px] text-brand-deep ring-1 ring-black/[0.06]">
                Switch to live
              </Link>
            </div>
          ) : null}

          <header className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-3 lg:hidden">
              <BrandTile className="h-11 w-11" />
            </div>
            <div className="min-w-0">
              <p className="text-[13px] font-medium text-ink-muted">Admin console</p>
              <h1 className="font-display text-[28px] font-semibold leading-tight tracking-tight text-ink">{current.label}</h1>
            </div>
            <div className="flex-1" aria-hidden />
            <SmsIndicator mode={mode} onClick={() => setSection("messaging")} />
          </header>

          <div>
            <div className="mb-4 flex items-end justify-between gap-4 px-1">
              <p className="text-[13px] text-ink-muted">{current.blurb}</p>
              {loading ? <Pill tone="muted" pulse>Loading</Pill> : null}
            </div>

            <div className="space-y-5">
              {section === "schedules" ? (
                <>
                  <RunsPanel entries={data.entries} runs={data.runs} actions={actions} now={now} settings={settings} />
                  <RecallSettingsPanel settings={settings?.recall ?? null} timezone={settings?.timezone} actions={actions} />
                </>
              ) : null}
              {section === "patients" ? (
                <PatientsPanel
                  entries={data.entries}
                  runs={data.runs}
                  actions={actions}
                  allowlist={allowlist}
                  settings={settings}
                  onManageNumbers={() => setSection("numbers")}
                />
              ) : null}
              {section === "numbers" ? (
                <AllowlistPanel allowlist={allowlist} consent={consent} actions={actions} error={allowlistError} />
              ) : null}
              {section === "messaging" ? (
                <>
                  <SmsNumberCard mode={mode} />
                  <TextLimitPanel live={actions.live} />
                  <TextConversationsPanel
                    sessions={data.sessions}
                    calls={data.calls}
                    error={followUpsError}
                    reads={reads}
                    now={now}
                    onOpenCall={diagnose}
                    timeZone={tz}
                  />
                  <BookedCallbacksPanel callbacks={data.callbacks} actions={actions} now={now} timeZone={tz} />
                </>
              ) : null}
              {section === "knowledge" ? <KnowledgePanel live={actions.live} timeZone={tz} /> : null}
              {section === "testing" ? (
                <>
                  <TestInBrowserPanel actions={actions} />
                  <TestByTextPanel live={actions.live} />
                </>
              ) : null}
              {section === "diagnostics" ? (
                <DiagnosticsPanel
                  calls={data.calls}
                  callsError={callsError}
                  reads={reads}
                  selected={diagnosed}
                  onSelect={setDiagnosed}
                  onRefresh={onRefreshCalls}
                  timeZone={tz}
                />
              ) : null}
              {section === "audit" ? <AuditPanel reads={reads} timeZone={tz} /> : null}
              {section === "branding" ? (
                <BrandingPanel live={actions.live} onOpenDisplay={() => setSection("display")} />
              ) : null}
              {section === "display" ? (
                <>
                  <CompanyNamePanel org={settings?.organization ?? null} actions={actions} />
                  <TimeDisplayPanel saved={settings?.display?.time_format ?? null} actions={actions} />
                </>
              ) : null}
            </div>
          </div>
        </main>
      </div>

      <MobileNav
        items={SECTIONS.map(({ key, short, icon }) => ({ key, label: short, icon, count: counts[key] }))}
        active={section}
        onSelect={setSection}
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
