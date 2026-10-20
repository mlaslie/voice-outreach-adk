"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Hand, MessageSquareText, RefreshCw, Search, Timer, Wrench } from "lucide-react";
import { AdminReads } from "@/lib/nurse/actions";
import { CallDoc, CallEventsResponse } from "@/lib/types";
import { TimelineRow, buildTimeline, formatMs, formatOffset } from "@/lib/diagnostics";
import { getStatusLabel, isActive } from "@/lib/status";
import { formatDuration, formatPhone } from "@/lib/format";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { EmptyState, Panel, Pill, Tone } from "@/components/nurse/parts";

/**
 * Admin → Call diagnostics: the gateway's millisecond timeline for one call,
 * for engineers (it replaces the classic dashboard's call HUD timeline).
 *
 * Names, statuses and timings only. It never shows answers or transcript
 * text: nurses have those in their console (lib/diagnostics.ts keeps only a
 * fixed list of detail keys).
 */

function statusTone(c: CallDoc): Tone {
  if (isActive(c)) return "teal";
  switch (c.call_status) {
    case "success": return "good";
    case "left_vmail": return "info";
    case "interrupted": return "urgent";
    case "failed": case "busy": return "warn";
    default: return "muted";
  }
}

export function DiagnosticsPanel({
  calls,
  callsError,
  reads,
  selected,
  onSelect,
  onRefresh,
  timeZone,
}: {
  calls: CallDoc[];
  callsError?: string | null;
  reads: Pick<AdminReads, "callEvents">;
  /** The selected call id (call_sid or doc id). */
  selected: string | null;
  onSelect: (id: string | null) => void;
  onRefresh?: () => void;
  timeZone?: string;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const sorted = useMemo(
    () => [...calls].sort((a, b) => (b.initiated_at ?? "").localeCompare(a.initiated_at ?? "")),
    [calls],
  );
  const shown = useMemo(() => {
    if (!q) return sorted;
    const digits = q.replace(/\D/g, "");
    return sorted.filter((c) =>
      (c.patient_name ?? "").toLowerCase().includes(q)
      || (c.patient_id ?? "").toLowerCase().includes(q)
      || (c.call_sid ?? c.doc_id).toLowerCase().includes(q)
      || (digits.length >= 3 && (c.to ?? "").replace(/\D/g, "").endsWith(digits)));
  }, [sorted, q]);
  const idOf = (c: CallDoc) => c.call_sid ?? c.doc_id;
  const call = selected ? calls.find((c) => idOf(c) === selected || c.doc_id === selected) ?? null : null;

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(18rem,24rem)_1fr]">
      <Panel className="flex max-h-[44rem] flex-col overflow-hidden">
        <div className="space-y-3 border-b border-black/[0.05] px-5 py-3.5">
          <div className="flex items-center gap-3">
            <h3 className="flex-1 font-display text-[15px] font-semibold text-ink">Recent calls</h3>
            <span className="text-[12px] text-ink-muted">{shown.length}{q ? ` of ${sorted.length}` : ""}</span>
            {onRefresh ? (
              <button type="button" onClick={onRefresh} aria-label="Refresh the call list"
                      className="grid h-8 w-8 place-items-center rounded-full text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name, MRN, last digits or call id"
              aria-label="Search calls"
              className="h-10 w-full rounded-xl bg-white pl-9 pr-3 text-[14px] text-ink ring-1 ring-black/[0.08] placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-brand/40"
            />
          </label>
        </div>
        {callsError ? (
          <p className="border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark">{callsError}</p>
        ) : null}
        <ul className="min-h-0 flex-1 divide-y divide-black/[0.05] overflow-y-auto">
          {shown.length === 0 ? (
            <li><EmptyState title={q ? "No calls match" : "No calls yet"} hint={q ? "Try a name, an MRN or the last digits of the number." : undefined} /></li>
          ) : shown.map((c) => {
            const id = idOf(c);
            const active = call ? idOf(call) === id : false;
            return (
              <li key={c.doc_id}>
                <button
                  type="button"
                  onClick={() => onSelect(id)}
                  aria-current={active ? "true" : undefined}
                  className={cn("flex w-full items-center gap-3 px-5 py-3 text-left transition-colors",
                    active ? "bg-brand/[0.08]" : "hover:bg-brand/[0.03]")}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-display text-[14px] font-semibold text-ink">{c.patient_name || "Unknown patient"}</div>
                    <div className="text-[12px] tabular-nums text-ink-muted">
                      {fmtDateTime(c.initiated_at, { month: "short", day: "numeric", timeZone })} · {formatPhone(c.to)}
                    </div>
                  </div>
                  {c.channel === "sms" ? <Pill tone="info" icon={MessageSquareText}>Text</Pill> : null}
                  <Pill tone={statusTone(c)}>{isActive(c) ? "In progress" : getStatusLabel(c.call_status)}</Pill>
                </button>
              </li>
            );
          })}
        </ul>
      </Panel>

      {call ? (
        <CallDiagnostics key={idOf(call)} call={call} reads={reads} timeZone={timeZone} />
      ) : (
        <Panel>
          <EmptyState title="Pick a call" hint="Its timings, tool calls, interruptions and errors show here. Answers and transcripts stay in the nurse console." />
        </Panel>
      )}
    </div>
  );
}

function CallDiagnostics({ call, reads, timeZone }: { call: CallDoc; reads: Pick<AdminReads, "callEvents">; timeZone?: string }) {
  const id = call.call_sid ?? call.doc_id;
  const [data, setData] = useState<CallEventsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    reads.callEvents(id)
      .then((r) => { if (alive) setData(r); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "Could not load the timeline."); });
    return () => { alive = false; };
  }, [id, reads, n]);

  const rows = useMemo(() => (data?.available ? buildTimeline(data.timeline ?? []) : []), [data]);
  const s = data?.summary;
  const failure = call.failure_code
    ? `${call.failure_code}${call.failure_message ? `: ${call.failure_message}` : ""}`
    : call.failure_reason ?? null;

  return (
    <div className="min-w-0 space-y-5">
      <Panel className="px-5 py-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-display text-[17px] font-semibold text-ink">{call.patient_name || "Unknown patient"}</h3>
            <p className="break-all font-mono text-[11px] text-ink-muted">{id}</p>
          </div>
          <Pill tone={statusTone(call)}>{isActive(call) ? "In progress" : getStatusLabel(call.call_status)}</Pill>
          <button type="button" onClick={() => setN((x) => x + 1)} aria-label="Reload the timeline"
                  className="grid h-8 w-8 place-items-center rounded-full text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px] sm:grid-cols-4">
          <Fact label="Started" value={fmtDateTime(call.initiated_at, { month: "short", day: "numeric", timeZone, seconds: true })} />
          <Fact label="Answered" value={call.answered_at ? fmtDateTime(call.answered_at, { timeZone, seconds: true }) : "—"} />
          <Fact label="Ended" value={call.ended_at ? fmtDateTime(call.ended_at, { timeZone, seconds: true }) : "—"} />
          <Fact label="Length" value={formatDuration(call.duration_seconds)} />
        </dl>
        {failure ? <p className="mt-2 rounded-xl bg-brand-orange/15 px-3 py-1.5 text-[12px] text-ink">Carrier: {failure}</p> : null}
      </Panel>

      <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
        <Tile icon={Timer} label="First audio" value={s ? formatMs(s.first_audio_ms) : "—"} sub="media start to the agent's first word" />
        <Tile icon={Wrench} label="Tool calls" value={s ? String(s.tool_calls) : "—"} />
        <Tile icon={Hand} label="Interruptions" value={s ? String(s.interruptions) : "—"} sub="patient spoke over the agent" />
        <Tile icon={AlertTriangle} label="Errors" value={s ? String(s.errors) : "—"} sub="Live session (live.*)" alert={!!s?.errors} />
      </div>

      <Panel className="overflow-hidden">
        <div className="flex items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
          <Activity className="h-4 w-4 text-brand" />
          <h3 className="flex-1 font-display text-[15px] font-semibold text-ink">Timeline</h3>
          {s ? <span className="text-[12px] text-ink-muted">{s.events} events · offsets from audio start</span> : null}
        </div>
        {error ? (
          <p className="px-5 py-6 text-[13px] text-brand-red-dark">{error}</p>
        ) : !data ? (
          <p className="px-5 py-6 text-[13px] text-ink-muted">Loading…</p>
        ) : !data.available || rows.length === 0 ? (
          <EmptyState
            title="No timeline recorded"
            hint={call.channel === "sms"
              ? "This call was answered by text, so the voice gateway recorded nothing."
              : "Calls answered by text, and calls that never connected (no answer, busy), have no gateway timeline."}
          />
        ) : (
          <ol className="relative px-5 py-4">
            <span className="absolute bottom-6 left-[calc(7.75rem+4px)] top-6 w-px bg-black/[0.08]" aria-hidden />
            {rows.map((r) => <Row key={r.key} row={r} />)}
          </ol>
        )}
      </Panel>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="truncate font-semibold tabular-nums text-ink">{value}</dd>
    </div>
  );
}

function Tile({ icon: Icon, label, value, sub, alert }: {
  icon: React.ComponentType<{ className?: string }>; label: string; value: string; sub?: string; alert?: boolean;
}) {
  return (
    <Panel className="p-4">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-muted">
        <Icon className="h-3.5 w-3.5" /> {label}
      </div>
      <div className={cn("mt-1.5 font-display text-[28px] font-semibold leading-none tabular-nums", alert ? "text-brand-red-dark" : "text-ink")}>
        {value}
      </div>
      {sub ? <div className="mt-1.5 text-[11px] text-ink-soft">{sub}</div> : null}
    </Panel>
  );
}

const DOT: Record<TimelineRow["kind"], string> = {
  milestone: "bg-brand ring-brand/20",
  event: "bg-ink-muted/60 ring-black/[0.04]",
  tool: "bg-brand-blue ring-brand-blue/20",
  error: "bg-brand-red ring-brand-red/25",
};

function Row({ row }: { row: TimelineRow }) {
  const err = row.kind === "error";
  const toolFailed = row.tool?.status && !["ok", "success", "verified", "saved"].includes(row.tool.status.toLowerCase());
  return (
    <li className="relative flex items-start gap-3 py-1.5">
      <span className={cn("w-[5.75rem] shrink-0 pt-0.5 text-right font-mono text-[12px] tabular-nums",
        err ? "font-semibold text-brand-red-dark" : "text-ink-soft")}>
        {formatOffset(row.offsetMs)}
      </span>
      <span className={cn("relative z-10 mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ring-4", DOT[row.kind])} aria-hidden />
      <div className={cn("min-w-0 flex-1 rounded-xl px-2.5 py-1", err && "bg-brand-red/10 ring-1 ring-brand-red/25")}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className={cn("text-[13px]", row.kind === "milestone" ? "font-semibold text-ink" : err ? "font-semibold text-brand-red-dark" : "text-ink")}>
            {row.label}
          </span>
          <span className="font-mono text-[11px] text-ink-muted">{row.event}</span>
          {row.tool ? (
            row.tool.status === null ? (
              <Pill tone="warn">No response</Pill>
            ) : (
              <Pill tone={toolFailed ? "warn" : "good"}>
                {row.tool.status}{row.tool.durationMs !== null ? ` · ${formatMs(row.tool.durationMs)}` : ""}
              </Pill>
            )
          ) : null}
        </div>
        {row.detail ? <div className={cn("text-[12px]", err ? "text-brand-red-dark" : "text-ink-soft")}>{row.detail}</div> : null}
      </div>
    </li>
  );
}
