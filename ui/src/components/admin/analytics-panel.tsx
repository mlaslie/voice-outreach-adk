"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtDateTime } from "@/lib/time";
import { CallDoc, CallListEntry } from "@/lib/types";
import {
  AnalyticsSummary,
  notCompletedText,
  outcomeRows,
  Channel,
  PAIN_BUCKETS,
  SYMPTOMS,
  TIER_LABEL,
  TIER_ORDER,
  TierKey,
  fetchSummary,
  minutes,
  pct,
  sampleSummary,
  share,
} from "@/lib/analytics";
import { Panel, Pill } from "@/components/nurse/parts";

/**
 * Analytics (docs/ANALYTICS-PLAN.md, phase 1), in the admin console: call
 * outcomes, outreach, clinical picture, nurse workload. The numbers come de-identified from the analytics service;
 * names are resolved here from the call list and the calls this console holds.
 */

const RANGES = [7, 30, 90] as const;
const CHANNELS: { key: Channel; label: string }[] = [
  { key: "all", label: "All" },
  { key: "voice", label: "Voice" },
  { key: "sms", label: "Text" },
];
const TIER_COLOR: Record<TierKey, string> = {
  emergency: "bg-brand-red",
  urgent_same_day: "bg-brand-coral",
  routine_next_business_day: "bg-brand-yellow",
  informational: "bg-brand/50",
  none: "bg-ink/15",
};

export function AnalyticsPanel({
  mode,
  entries,
  calls,
  onOpenCall,
}: {
  mode: "demo" | "live";
  entries: CallListEntry[];
  calls: CallDoc[];
  onOpenCall: (callId: string) => void;
}) {
  const [days, setDays] = useState<number>(30);
  const [channel, setChannel] = useState<Channel>("all");
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (mode === "demo") {
      setData(sampleSummary(days, channel));
      setError(null);
      return;
    }
    const ctl = new AbortController();
    setLoading(true);
    fetchSummary(days, channel, ctl.signal)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    return () => ctl.abort();
  }, [mode, days, channel, tick]);

  const entryName = useMemo(() => new Map(entries.map((e) => [e.entry_id, e.patient_name])), [entries]);
  const callName = useMemo(
    () => new Map(calls.map((c) => [c.doc_id, c.patient_name ?? "Patient"])), [calls]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          options={RANGES.map((d) => ({ key: String(d), label: `${d} days` }))}
          value={String(days)}
          onChange={(v) => setDays(Number(v))}
        />
        <Segmented options={CHANNELS} value={channel} onChange={(v) => setChannel(v as Channel)} />
        <div className="ml-auto flex items-center gap-2 text-[12px] text-ink-muted">
          {data?.refreshed_at ? <span>Data as of {fmtDateTime(data.refreshed_at)}</span> : null}
          {loading ? <Pill tone="muted" pulse>Loading</Pill> : null}
          {mode === "live" ? (
            <button
              type="button"
              onClick={() => setTick((t) => t + 1)}
              className="inline-flex items-center gap-1 rounded-full bg-white px-3 py-1 font-semibold text-ink ring-1 ring-black/[0.06]"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div role="alert" className="flex items-center gap-3 rounded-2xl bg-brand-red/10 px-4 py-2.5 text-[13px] font-semibold text-brand-red-dark ring-1 ring-brand-red/30">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      ) : null}

      {data ? (
        <>
          <Outcomes data={data} />
          <Outreach data={data} entryName={entryName} />
          <Clinical data={data} callName={callName} onOpenCall={onOpenCall} />
          <Workload data={data} />
          <p className="px-1 text-[12px] text-ink-muted">
            {data.range.calls} calls from {data.range.start_day} to {data.range.end_day}. Figures are refreshed every
            15 minutes from de-identified records; the call lists stay the place to act.
          </p>
        </>
      ) : null}
    </div>
  );
}

function Segmented({
  options, value, onChange,
}: { options: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="inline-flex rounded-full bg-white/80 p-1 ring-1 ring-black/[0.06]">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          aria-pressed={o.key === value}
          onClick={() => onChange(o.key)}
          className={cn(
            "rounded-full px-3 py-1 text-[12px] font-semibold transition-colors",
            o.key === value ? "bg-brand text-white" : "text-ink-soft hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Section({ title, blurb, children }: { title: string; blurb: string; children: React.ReactNode }) {
  return (
    <Panel className="p-5">
      <h3 className="font-display text-lg font-semibold text-ink">{title}</h3>
      <p className="mb-4 text-[13px] text-ink-muted">{blurb}</p>
      {children}
    </Panel>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl bg-white/80 px-4 py-3 ring-1 ring-black/[0.04]">
      <div className="text-[12px] font-semibold text-ink-muted">{label}</div>
      <div className="font-display text-2xl font-semibold tabular-nums text-ink">{value}</div>
      {sub ? <div className="text-[12px] text-ink-muted">{sub}</div> : null}
    </div>
  );
}

function Bar({ label, value, whole, color = "bg-brand", right }: {
  label: string; value: number; whole: number; color?: string; right?: string;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-[13px]">
      <span className="truncate text-ink-soft">{label}</span>
      <span className="h-2.5 overflow-hidden rounded-full bg-ink/[0.06]">
        <span className={cn("block h-full rounded-full", color)} style={{ width: `${share(value, whole)}%` }} />
      </span>
      <span className="w-16 text-right font-semibold tabular-nums text-ink">{right ?? value}</span>
    </div>
  );
}

function Outcomes({ data }: { data: AnalyticsSummary }) {
  const o = data.outcomes;
  if (!o) return null;
  const rows = outcomeRows(o);
  return (
    <Section title="Call outcomes" blurb="How every call in this period ended. Each call is counted once, so the rows add up to the total.">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {rows.slice(0, 5).map((r) => (
          <Stat key={r.key} label={r.label} value={String(r.count)}
                sub={[pct(o.total ? r.count / o.total : null), r.sub].filter(Boolean).join(" · ")} />
        ))}
      </div>
      <div className="mt-5 space-y-2">
        {rows.map((r) => (
          <Bar key={r.key} label={r.label} value={r.count} whole={o.total} color={r.color}
               right={`${r.count} · ${pct(o.total ? r.count / o.total : null)}`} />
        ))}
        <div className="flex justify-between border-t border-black/[0.06] pt-2 text-[13px] font-semibold text-ink">
          <span>Total calls</span><span className="tabular-nums">{o.total}</span>
        </div>
      </div>
      {o.not_completed.total ? (
        <p className="mt-3 text-[12px] text-ink-muted">
          Not completed: {notCompletedText(o.not_completed.by_outcome)}
        </p>
      ) : null}
    </Section>
  );
}

function Outreach({ data, entryName }: { data: AnalyticsSummary; entryName: Map<string, string> }) {
  const o = data.outreach;
  const f = o.voice_funnel;
  const steps: [string, number][] = [
    ["Dialled", f.dialled], ["Answered", f.connected], ["A person answered", f.human],
    ["Identity verified", f.verified], ["Questions completed", f.completed],
  ];
  return (
    <Section title="Outreach" blurb="Did we reach the patients we set out to call?">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Patients reached" value={`${o.patients_reached} / ${o.patients_attempted}`} sub={pct(o.reach_rate)} />
        <Stat label="Attempts per patient reached" value={o.attempts_per_reached === null ? "–" : o.attempts_per_reached.toFixed(1)} />
        <Stat label="Text invitations sent" value={String(o.text_funnel.invites_sent)} />
        <Stat label="Completed by text" value={String(o.text_funnel.completed_by_text)}
              sub={o.text_funnel.invites_sent ? pct(o.text_funnel.completed_by_text / o.text_funnel.invites_sent) : undefined} />
      </div>
      <div className="mt-5 space-y-2">
        <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Voice calls</div>
        {steps.map(([label, n]) => (
          <Bar key={label} label={label} value={n} whole={f.dialled} right={`${n} · ${pct(f.dialled ? n / f.dialled : null)}`} />
        ))}
      </div>
      {o.unreached.length ? (
        <div className="mt-5">
          <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-muted">
            Not reached yet ({o.unreached.length})
          </div>
          <ul className="divide-y divide-black/[0.05] rounded-2xl bg-white/80 ring-1 ring-black/[0.04]">
            {o.unreached.slice(0, 12).map((u, i) => (
              <li key={`${u.entry_id ?? u.patient_key ?? i}`} className="flex items-center gap-3 px-4 py-2 text-[13px]">
                <span className="font-semibold text-ink">{(u.entry_id && entryName.get(u.entry_id)) || "Patient no longer on the call list"}</span>
                <span className="ml-auto text-ink-muted">
                  {u.attempts} {u.attempts === 1 ? "attempt" : "attempts"}{u.last_at ? ` · last ${fmtDateTime(u.last_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Section>
  );
}

function Clinical({ data, callName, onOpenCall }: {
  data: AnalyticsSummary; callName: Map<string, string>; onOpenCall: (id: string) => void;
}) {
  const c = data.clinical;
  const maxDay = Math.max(1, ...c.tiers_by_day.map((d) => TIER_ORDER.reduce((s, t) => s + (d[t] ?? 0), 0)));
  const maxReason = Math.max(1, ...c.top_reasons.map((r) => r.count));
  const pain = PAIN_BUCKETS.map(([k]) => c.symptoms[k] ?? 0);
  const painTotal = pain.reduce((a, b) => a + b, 0);
  const cats = Object.entries(c.questions.by_category).sort((a, b) => b[1] - a[1]);
  const maxCat = Math.max(1, ...cats.map(([, n]) => n));
  return (
    <Section title="Clinical picture" blurb="What patients are reporting, and how urgent it is.">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Follow-up tier by day</div>
      <div className="mt-2 flex h-36 items-end gap-1" role="img" aria-label="Calls per day, stacked by follow-up tier">
        {c.tiers_by_day.map((d) => (
          <div key={d.day} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={d.day}>
            {[...TIER_ORDER].reverse().map((t) =>
              d[t] ? (
                <span key={t} className={cn("block w-full first:rounded-t-md", TIER_COLOR[t])}
                      style={{ height: `${share(d[t] ?? 0, maxDay)}%` }} />
              ) : null,
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-[12px] text-ink-soft">
        {TIER_ORDER.map((t) => (
          <span key={t} className="inline-flex items-center gap-1.5">
            <span className={cn("h-2.5 w-2.5 rounded-sm", TIER_COLOR[t])} /> {TIER_LABEL[t]}
          </span>
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Top follow-up reasons</div>
          {c.top_reasons.length ? c.top_reasons.map((r) => (
            <Bar key={`${r.text}|${r.tier}`} label={r.text} value={r.count} whole={maxReason} color={TIER_COLOR[r.tier] ?? "bg-brand"} />
          )) : <p className="text-[13px] text-ink-muted">None in this range.</p>}
        </div>
        <div className="space-y-2">
          <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Reported on completed calls</div>
          {SYMPTOMS.map(([k, label]) => (
            <Bar key={k} label={label} value={c.symptoms[k] ?? 0} whole={Math.max(1, painTotal)} />
          ))}
          <div className="pt-2 text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Pain score</div>
          {PAIN_BUCKETS.map(([k, label], i) => (
            <Bar key={k} label={label} value={pain[i]} whole={painTotal} color={i === 3 ? "bg-brand-coral" : "bg-brand"}
                 right={`${pain[i]} · ${pct(painTotal ? pain[i] / painTotal : null)}`} />
          ))}
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Patient questions by topic</div>
          {cats.length ? cats.map(([k, n]) => (
            <Bar key={k} label={k.replace(/_/g, " ")} value={n} whole={maxCat} />
          )) : <p className="text-[13px] text-ink-muted">No questions in this range.</p>}
          <p className="pt-1 text-[12px] text-ink-muted">
            {Object.entries(c.questions.by_priority)
              .map(([k, n]) => `${TIER_LABEL[k as TierKey] ?? k.replace(/_/g, " ")}: ${n}`).join(" · ")}
          </p>
        </div>
        <div className="space-y-2">
          <div className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Emergencies</div>
          {c.emergencies.length ? (
            <ul className="space-y-1">
              {c.emergencies.map((id) => (
                <li key={id}>
                  <button type="button" onClick={() => onOpenCall(id)}
                          className="text-[13px] font-semibold text-brand-red-dark underline-offset-2 hover:underline">
                    {callName.get(id) ?? "Open the call"}
                    <span className="ml-1 font-normal text-ink-muted">(Call diagnostics)</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="text-[13px] text-ink-muted">None in this range.</p>}
        </div>
      </div>
    </Section>
  );
}

function Workload({ data }: { data: AnalyticsSummary }) {
  const w = data.workload;
  const tiers: TierKey[] = ["emergency", "urgent_same_day", "routine_next_business_day"];
  return (
    <Section title="Nurse workload" blurb="What is waiting, and how quickly follow-ups are signed off.">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiers.map((t) => (
          <Stat key={t} label={`Open: ${TIER_LABEL[t]}`} value={String(w.open_by_tier[t] ?? 0)}
                sub={w.overdue_by_tier?.[t] ? `${w.overdue_by_tier[t]} overdue` : undefined} />
        ))}
        <Stat label="Tier changed by a nurse" value={pct(w.override.rate)}
              sub={`${w.override.up} raised · ${w.override.down} lowered of ${w.override.n}`} />
      </div>
      <table className="mt-5 w-full text-[13px]">
        <thead>
          <tr className="text-left text-[12px] font-semibold uppercase tracking-wide text-ink-muted">
            <th className="py-1 font-semibold">Time to sign-off</th>
            <th className="py-1 text-right font-semibold">Median</th>
            <th className="py-1 text-right font-semibold">90th percentile</th>
            <th className="py-1 text-right font-semibold">Signed off</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-black/[0.05]">
          {tiers.map((t) => {
            const s = w.signoff_minutes[t];
            return (
              <tr key={t}>
                <td className="py-1.5 text-ink-soft">{TIER_LABEL[t]}</td>
                <td className="py-1.5 text-right font-semibold tabular-nums">{minutes(s?.median)}</td>
                <td className="py-1.5 text-right tabular-nums">{minutes(s?.p90)}</td>
                <td className="py-1.5 text-right tabular-nums">{s?.n ?? 0}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
  );
}
