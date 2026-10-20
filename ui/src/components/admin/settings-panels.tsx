"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Building2, Clock, MessageSquareText } from "lucide-react";
import { AdminActions } from "@/lib/nurse/actions";
import { OrganizationSettings } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Panel } from "@/components/nurse/parts";
import { fmtHHMM, TimeFormat, useTimeFormat } from "@/lib/time";
import { fetchSettings, setSetting } from "@/lib/followups";

/** Admin → Display: 12- or 24-hour clocks for nurses. */
export function TimeDisplayPanel({ saved, actions }: { saved: TimeFormat | null; actions: Pick<AdminActions, "live" | "saveDisplay"> }) {
  const current = useTimeFormat();
  const value = saved ?? current;
  const [busy, setBusy] = useState(false);
  const choose = async (f: TimeFormat) => {
    if (f === value || busy) return;
    setBusy(true);
    try {
      await actions.saveDisplay({ time_format: f });
      if (actions.live) toast.success(f === "24h" ? "Times now show as 24-hour" : "Times now show as 12-hour");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the setting.");
    } finally {
      setBusy(false);
    }
  };
  const OPTIONS: { f: TimeFormat; label: string }[] = [
    { f: "12h", label: `12-hour · ${fmtHHMM("14:30", "12h")}` },
    { f: "24h", label: `24-hour · ${fmtHHMM("14:30", "24h")}` },
  ];
  return (
    <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand/10 text-brand-deep">
        <Clock className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-display text-[15px] font-semibold text-ink">Time display</h3>
        <p className="text-[12px] text-ink-muted">
          How times show for nurses in this console and the Gemini Enterprise agent. Patients always hear and read 12-hour times.
        </p>
      </div>
      <div role="radiogroup" aria-label="Time display" className="ml-auto inline-flex rounded-full bg-mist/70 p-1 ring-1 ring-inset ring-black/[0.06]">
        {OPTIONS.map(({ f, label }) => (
          <button
            key={f}
            type="button"
            role="radio"
            aria-checked={value === f}
            disabled={busy}
            onClick={() => choose(f)}
            className={cn("rounded-full px-3.5 py-1.5 text-[12px] font-semibold tabular-nums transition-colors disabled:opacity-60",
              value === f ? "bg-brand-deep text-white shadow-sm" : "text-ink-soft hover:text-ink")}
          >
            {label}
          </button>
        ))}
      </div>
    </Panel>
  );
}

/**
 * Admin → Messaging → Missed-call texts: the one-text-per-number-per-24-hours limit.
 * It protects patients from repeat texts; turn it off to re-run a demo on the
 * same test phone. Skipped texts say why on the call ("Text not sent").
 */
export function TextLimitPanel({ live }: { live: boolean }) {
  const [on, setOn] = useState<boolean | null>(live ? null : true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!live) return;
    fetchSettings()
      .then((r) => setOn(r.settings.invite_cooldown_enabled))
      .catch(() => setOn(null));
  }, [live]);
  const choose = async (next: boolean) => {
    if (next === on || busy) return;
    if (!live) {
      setOn(next);
      toast.info("Sample data: nothing is changed.");
      return;
    }
    setBusy(true);
    try {
      const r = await setSetting("invite_cooldown_enabled", next);
      setOn(r.settings.invite_cooldown_enabled);
      toast.success(next ? "One text per number per 24 hours" : "Limit off: every missed call gets a text");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the setting.");
    } finally {
      setBusy(false);
    }
  };
  const OPTIONS: { v: boolean; label: string }[] = [
    { v: true, label: "1 per 24 h" },
    { v: false, label: "Every missed call" },
  ];
  return (
    <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand/10 text-brand-deep">
        <MessageSquareText className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-display text-[15px] font-semibold text-ink">Missed-call texts</h3>
        <p className="text-[12px] text-ink-muted">
          {on === false
            ? "Limit off: every missed call sends a text, even to a number texted today. Turn it back on after testing."
            : "A number gets at most one follow-up text per 24 hours, so patients are not texted repeatedly."}
        </p>
      </div>
      <div role="radiogroup" aria-label="Missed-call texts" className="ml-auto inline-flex rounded-full bg-mist/70 p-1 ring-1 ring-inset ring-black/[0.06]">
        {OPTIONS.map(({ v, label }) => (
          <button
            key={label}
            type="button"
            role="radio"
            aria-checked={on === v}
            disabled={busy || on === null}
            onClick={() => choose(v)}
            className={cn("rounded-full px-3.5 py-1.5 text-[12px] font-semibold transition-colors disabled:opacity-60",
              on === v ? "bg-brand-deep text-white shadow-sm" : "text-ink-soft hover:text-ink")}
          >
            {label}
          </button>
        ))}
      </div>
    </Panel>
  );
}


/** The same rule as the API (voa_core.organization): spoken and texted, GSM-7 only. */
export const ORG_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 &'.,()/+-]{1,59}$/;

/** Admin → Display: the company name patients hear on calls and read in texts. */
export function CompanyNamePanel({
  org,
  actions,
}: {
  org: OrganizationSettings | null;
  actions: Pick<AdminActions, "live" | "saveOrganization">;
}) {
  const [draft, setDraft] = useState(org?.org_name ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(org?.org_name ?? ""), [org?.org_name]);
  const clean = draft.trim().replace(/\s+/g, " ");
  const valid = ORG_NAME_RE.test(clean);
  const changed = !!org && clean !== org.org_name;
  const save = async (name: string) => {
    setBusy(true);
    try {
      await actions.saveOrganization(name);
      if (actions.live) toast.success(name ? `Company name set to ${name}` : "Company name reset to the default");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the company name.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel className="space-y-3 px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
          <Building2 className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Company name</h3>
          <p className="text-[12px] text-ink-muted">
            Used in the call script (&ldquo;an automated call from …&rdquo;), the voicemail message and every text
            message. Changes apply to new calls and texts within a minute.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="org-name" className="sr-only">Company name</label>
        <input
          id="org-name"
          value={draft}
          maxLength={60}
          disabled={busy || !org}
          onChange={(e) => setDraft(e.target.value)}
          className="h-9 min-w-0 flex-1 rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
        />
        <button
          type="button"
          disabled={busy || !changed || !valid}
          onClick={() => save(clean)}
          className="rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          disabled={busy || !org || org.is_default}
          onClick={() => save("")}
          title={org ? `Back to the configured name: ${org.default}` : undefined}
          className="rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-50"
        >
          Reset to default
        </button>
      </div>
      {draft && !valid ? (
        <p className="text-[12px] text-brand-red-dark" role="alert">
          Use 2–60 letters, digits, spaces and &amp; &apos; . , ( ) / + - (the name is spoken and texted).
        </p>
      ) : null}
      <p className="rounded-xl bg-brand-orange/10 px-3 py-2 text-[12px] text-ink-soft">
        The name is part of the SMS consent wording and your A2P 10DLC (carrier) registration. Texts sent under a name
        that differs from the registered brand can be filtered by carriers, so update the registration when this changes.
        {org && !org.is_default ? ` Configured default: ${org.default}.` : ""}
      </p>
    </Panel>
  );
}
