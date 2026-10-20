"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import {
  AlertTriangle,
  CalendarClock,
  Clock,
  FlaskConical,
  MessageSquareText,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
import { useTimeFormat } from "@/lib/time";
import { AdminActions } from "@/lib/nurse/actions";
import { AllowlistResponse, CallListEntry, CallRun, CallSettingsResponse } from "@/lib/types";
import { BrandTile, Panel, Pill } from "@/components/nurse/parts";
import { Backdrop, MobileNav, NavButton, NavIcon, SidebarLink, SidebarShell } from "@/components/console-shell";
import { SmsIndicator } from "@/components/sms-indicator";
import { SmsTestDialog } from "@/components/sms-test-dialog";
import { RecallSettingsPanel } from "@/components/nurse/recall-settings";
import { SmsNumberCard } from "@/components/nurse/sms-number-card";
import { RunsPanel } from "./runs-panel";
import { PatientsPanel } from "./patients-panel";
import { AllowlistPanel, ConsentMap } from "./allowlist-panel";
import { CompanyNamePanel, TextLimitPanel, TimeDisplayPanel } from "./settings-panels";

export type Section = "schedules" | "patients" | "numbers" | "messaging" | "display";

const SECTIONS: { key: Section; label: string; short: string; blurb: string; icon: NavIcon }[] = [
  { key: "schedules", label: "Schedules", short: "Runs", icon: CalendarClock,
    blurb: "Call runs, and the clinic's calling hours and auto-recall." },
  { key: "patients", label: "Patients", short: "Patients", icon: Users,
    blurb: "The patients the agent will call, with every detail it needs." },
  { key: "numbers", label: "Authorized numbers", short: "Numbers", icon: ShieldCheck,
    blurb: "The numbers the agent may dial, and which of them agreed to texts." },
  { key: "messaging", label: "Messaging", short: "Texts", icon: MessageSquareText,
    blurb: "The shared SMS number, the missed-call text limit, and a dry-run text test." },
  { key: "display", label: "Display", short: "Display", icon: Clock,
    blurb: "The company name patients hear and read, and how times show for nurses." },
];

export interface AdminData {
  entries: CallListEntry[];
  runs: CallRun[];
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
  allowlist,
  allowlistError,
  consent,
  settings,
  unavailable = [],
  nurseConsoleUrl = null,
}: {
  data: AdminData;
  mode: "demo" | "live";
  loading?: boolean;
  actions: AdminActions;
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
  const [smsTestOpen, setSmsTestOpen] = useState(false);
  const now = useNow();
  const current = SECTIONS.find((s) => s.key === section)!;

  const counts: Partial<Record<Section, number>> = {
    schedules: data.runs.filter((r) => r.status === "scheduled" || r.status === "dispatching").length,
    patients: data.entries.length,
    numbers: allowlist?.count,
  };

  const openSmsTest = () => {
    if (!actions.live) {
      toast.info("Sample data: the text test runs against the live backend only. Switch to live data to try it.");
      return;
    }
    setSmsTestOpen(true);
  };

  return (
    <div className="relative min-h-[calc(100vh-37px)] overflow-hidden bg-[#f4f6f5] font-sans text-ink">
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
                  <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                    <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand/10 text-brand-deep">
                      <MessageSquareText className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="font-display text-[15px] font-semibold text-ink">Test by text</h3>
                      <p className="text-[12px] text-ink-muted">
                        Play the patient in the text follow-up, end to end. Dry run: nothing is sent to a phone.
                      </p>
                    </div>
                    <button
                      onClick={openSmsTest}
                      className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink"
                    >
                      <MessageSquareText className="h-3.5 w-3.5" /> Test by text
                    </button>
                  </Panel>
                </>
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
      {actions.live ? <SmsTestDialog open={smsTestOpen} onOpenChange={setSmsTestOpen} /> : null}
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
