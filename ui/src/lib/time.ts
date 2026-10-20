import { useSyncExternalStore } from "react";

/**
 * How nurse-facing times render: a clinic setting (admin console → Display),
 * "12h" ("2:30 PM") or "24h" ("14:30"). Patients are not affected — their
 * calls and texts always use 12-hour times.
 *
 * A tiny external store so plain helpers (fmtClock & co.) can read the
 * current format without threading it through props, and React trees that
 * call useTimeFormat() re-render when it changes. No browser storage is
 * touched at module scope (Node 22+ exposes a broken localStorage during SSR).
 */

export type TimeFormat = "12h" | "24h";
export const DEFAULT_TIME_FORMAT: TimeFormat = "24h";

let current: TimeFormat = DEFAULT_TIME_FORMAT;
const listeners = new Set<() => void>();

export function isTimeFormat(v: unknown): v is TimeFormat {
  return v === "12h" || v === "24h";
}

export function getTimeFormat(): TimeFormat {
  return current;
}

export function setTimeFormat(f: TimeFormat | null | undefined): void {
  const next = isTimeFormat(f) ? f : DEFAULT_TIME_FORMAT;
  if (next === current) return;
  current = next;
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** The current format; re-renders the caller when it changes. */
export function useTimeFormat(): TimeFormat {
  return useSyncExternalStore(subscribe, getTimeFormat, () => DEFAULT_TIME_FORMAT);
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

type When = string | number | Date;

function toDate(v: When): Date {
  return v instanceof Date ? v : new Date(v);
}

/** Newer ICU puts a narrow no-break space before AM/PM; keep output plain. */
function plainSpaces(s: string): string {
  return s.replace(/[\u202f\u00a0]/g, " ");
}

function clockOptions(format: TimeFormat, seconds: boolean): Intl.DateTimeFormatOptions {
  const base: Intl.DateTimeFormatOptions = format === "24h"
    ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
    : { hour: "numeric", minute: "2-digit", hour12: true };
  return seconds ? { ...base, second: "2-digit" } : base;
}

export interface ClockOptions {
  timeZone?: string;
  /** Include seconds ("14:30:05" / "2:30:05 PM"). */
  seconds?: boolean;
  /** Override the store (tests, exports). */
  format?: TimeFormat;
}

/** "14:30" or "2:30 PM". "—" for a missing/invalid value. */
export function fmtClock(v: When | null | undefined, opts: ClockOptions = {}): string {
  if (v == null || v === "") return "—";
  const d = toDate(v);
  if (isNaN(d.getTime())) return "—";
  const format = opts.format ?? current;
  return plainSpaces(new Intl.DateTimeFormat("en-US", {
    ...clockOptions(format, !!opts.seconds),
    ...(opts.timeZone ? { timeZone: opts.timeZone } : {}),
  }).format(d));
}

export interface DateTimeOptions extends ClockOptions {
  weekday?: "long" | "short" | "narrow";
  month?: "long" | "short" | "narrow" | "numeric" | "2-digit";
  day?: "numeric" | "2-digit";
  year?: "numeric" | "2-digit";
  /** Separator between the date part and the clock (default ", "). */
  sep?: string;
}

/** Date part (only the fields asked for) + clock: "Mon, Oct 19, 14:30". */
export function fmtDateTime(v: When | null | undefined, opts: DateTimeOptions = {}): string {
  if (v == null || v === "") return "—";
  const d = toDate(v);
  if (isNaN(d.getTime())) return "—";
  const { weekday, month, day, year, timeZone, sep = ", " } = opts;
  const hasDate = weekday || month || day || year;
  const clock = fmtClock(d, opts);
  if (!hasDate) return clock;
  const date = new Intl.DateTimeFormat("en-US", {
    ...(weekday ? { weekday } : {}),
    ...(month ? { month } : {}),
    ...(day ? { day } : {}),
    ...(year ? { year } : {}),
    ...(timeZone ? { timeZone } : {}),
  }).format(d);
  return `${plainSpaces(date)}${sep}${clock}`;
}

/** A wall-clock "HH:MM" (24-hour, e.g. a calling-window bound) in the
 *  current format: "14:30" or "2:30 PM". Unparseable input is returned as is. */
export function fmtHHMM(hhmm: string, format: TimeFormat = current): string {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm?.trim() ?? "");
  if (!m) return hhmm;
  const h = Number(m[1]);
  const mi = m[2];
  if (h > 23 || Number(mi) > 59) return hhmm;
  if (format === "24h") return `${String(h).padStart(2, "0")}:${mi}`;
  return `${((h + 11) % 12) + 1}:${mi} ${h < 12 ? "AM" : "PM"}`;
}

/** Split a formatted clock into its number and AM/PM suffix (empty in 24h). */
export function splitMeridiem(clock: string): { time: string; meridiem: string } {
  const m = /^(.*?)\s?(AM|PM)$/i.exec(clock);
  return m ? { time: m[1], meridiem: m[2].toUpperCase() } : { time: clock, meridiem: "" };
}
