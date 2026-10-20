import { CallEvent } from "./types";

/**
 * Admin → Call diagnostics: turn a call's raw gateway timeline into rows.
 *
 * - Offsets are "+ms" from media start (the first `media.start`), the moment
 *   audio began flowing; earlier events get a negative offset.
 * - A `tool.call` and the next `tool.response` with the same name become one
 *   row with the response's status and how long the tool took.
 * - `live.*` events are errors from the Live model session.
 * - Only a fixed list of detail keys is ever shown. The gateway records names
 *   and statuses only, but this view must never show an answer or transcript
 *   text even if a future event carried one.
 */

export type RowKind = "milestone" | "event" | "tool" | "error";

export interface TimelineRow {
  key: string;
  /** ms from media start (negative before it). */
  offsetMs: number;
  /** The raw event name ("tool.call"). */
  event: string;
  /** Plain words ("Tool call: verify_identity"). */
  label: string;
  /** Safe detail, "status: ok · reason: hangup", or "". */
  detail: string;
  kind: RowKind;
  /** For a grouped tool call: the response's status and the call's duration. */
  tool?: { name: string; status: string | null; durationMs: number | null };
}

/** Detail keys shown in the timeline. Anything else is dropped. */
export const SAFE_DETAIL_KEYS = ["name", "status", "verdict", "action", "phase", "reason", "seconds", "code"] as const;

const LABELS: Record<string, string> = {
  "media.start": "Audio started",
  "media.stop": "Audio stopped",
  "media.no_start": "Audio never started",
  "media.mark_ack": "Playback marker reached",
  "agent.audio_start": "Agent started speaking",
  "agent.turn_complete": "Agent finished a turn",
  "agent.interrupted": "Patient interrupted the agent",
  "agent.release": "Agent released the call",
  "amd.verdict": "Answering-machine check",
  "amd.late_machine_ignored": "Late machine verdict ignored",
  "amd.poll_error": "Answering-machine check failed",
  "vm.play": "Voicemail played",
  "vm.no_clip_hangup": "Hung up: no voicemail clip",
  "report.saved": "Report saved",
  "report.invalid": "Report rejected",
  "call.ended": "Call ended",
  "call.max_duration": "Call hit its time limit",
  "call.no_patient_context": "No patient details for the call",
  "hangup.auto_after_report": "Hung up after the report",
  "drain.mark_sent": "Waiting for audio to finish",
  "drain.timeout": "Audio drain timed out",
  "drain.turn_wait_timeout": "Turn wait timed out",
  "live.error": "Live session error",
  "live.event_error": "Live event error",
  "prompt.rendered": "Prompt built",
  "prompt.cache_hit": "Prompt from cache",
  "prompt.render_failed": "Prompt build failed",
  "persist.error": "Saving failed",
  "tool.call": "Tool call",
  "tool.response": "Tool response",
};

export function eventLabel(event: string): string {
  return LABELS[event] ?? event;
}

export function isErrorEvent(event: string): boolean {
  return event.startsWith("live.");
}

const MILESTONES = new Set(["media.start", "agent.audio_start", "report.saved", "call.ended"]);

function str(v: unknown): string | null {
  if (typeof v === "string") return v.length > 80 ? `${v.slice(0, 79)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return null;
}

/** "status: ok · reason: hangup" from the safe keys only (`skip` leaves some out). */
export function safeDetail(e: CallEvent, skip: readonly string[] = []): string {
  const parts: string[] = [];
  for (const k of SAFE_DETAIL_KEYS) {
    if (skip.includes(k)) continue;
    const v = str(e[k]);
    if (v !== null && v !== "") parts.push(`${k}: ${v}`);
  }
  return parts.join(" · ");
}

/** The ms that offsets count from: the first media.start, else the first event, else 0. */
export function originMs(events: CallEvent[]): number {
  const start = events.find((e) => e.event === "media.start");
  if (start && typeof start.ms === "number") return start.ms;
  return typeof events[0]?.ms === "number" ? events[0].ms : 0;
}

export function buildTimeline(events: CallEvent[]): TimelineRow[] {
  const sorted = events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => e && typeof e.event === "string" && typeof e.ms === "number")
    .sort((a, b) => a.e.ms - b.e.ms || a.i - b.i);
  const origin = originMs(sorted.map(({ e }) => e));
  const used = new Set<number>();
  const rows: TimelineRow[] = [];

  sorted.forEach(({ e, i }, pos) => {
    if (used.has(i)) return;
    const offsetMs = e.ms - origin;
    if (e.event === "tool.call") {
      const name = str(e.name) ?? "unknown tool";
      const match = sorted.slice(pos + 1).find(
        ({ e: r, i: ri }) => !used.has(ri) && r.event === "tool.response" && (str(r.name) ?? "unknown tool") === name,
      );
      if (match) used.add(match.i);
      const status = match ? str(match.e.status) : null;
      rows.push({
        key: `${i}`,
        offsetMs,
        event: e.event,
        label: `Tool call: ${name}`,
        detail: safeDetail(e, ["name"]),
        kind: "tool",
        tool: { name, status, durationMs: match ? match.e.ms - e.ms : null },
      });
      return;
    }
    const name = e.event === "tool.response" ? str(e.name) : null;
    rows.push({
      key: `${i}`,
      offsetMs,
      event: e.event,
      label: name ? `${eventLabel(e.event)}: ${name}` : eventLabel(e.event),
      detail: safeDetail(e, name ? ["name"] : []),
      kind: isErrorEvent(e.event) ? "error" : MILESTONES.has(e.event) ? "milestone" : "event",
    });
  });
  return rows;
}

/** "+1,234 ms" / "−120 ms" / "+0 ms". */
export function formatOffset(ms: number): string {
  const sign = ms < 0 ? "−" : "+";
  return `${sign}${Math.abs(Math.round(ms)).toLocaleString("en-US")} ms`;
}

/** "840 ms" / "2.4 s" for a summary tile; "—" when unknown. */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (Math.abs(ms) < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
