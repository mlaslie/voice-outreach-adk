import { RecallSettings } from "../types";

/**
 * Clinic calling hours — the Auto-recall window (Settings), which also limits
 * when a cancelled call can be rescheduled. Evaluated in the CLINIC's time
 * zone, not the browser's, so a nurse working from another zone sees the
 * same answer the proxy enforces.
 */

/** The shipped default calling hours (matches the proxy's RecallSettings()). */
export const DEFAULT_CALLING_HOURS: RecallSettings = {
  enabled: true,
  interval_minutes: 120,
  window_start: "09:00",
  window_end: "17:00",
  days: [0, 1, 2, 3, 4, 5],
  max_recalls: 2,
};
export const DEFAULT_CLINIC_TZ = "America/New_York";

const DAY_INDEX: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function clinicParts(ms: number, tz: string): { day: number; hhmm: string } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(ms));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return { day: DAY_INDEX[get("weekday")] ?? -1, hhmm: `${get("hour")}:${get("minute")}` };
}

export function inCallingWindow(iso: string, w: RecallSettings, tz: string): boolean {
  const ms = new Date(iso).getTime();
  if (isNaN(ms)) return false;
  const { day, hhmm } = clinicParts(ms, tz);
  return w.days.includes(day) && w.window_start <= hhmm && hhmm < w.window_end;
}

/** The first quarter-hour at or after `fromMs` inside the window, as ISO. */
export function nextCallingSlot(fromMs: number, w: RecallSettings, tz: string): string | null {
  const step = 15 * 60_000;
  let t = Math.ceil(fromMs / step) * step;
  for (let i = 0; i < 14 * 24 * 4; i++, t += step) {
    const iso = new Date(t).toISOString();
    if (inCallingWindow(iso, w, tz)) return iso;
  }
  return null;
}

/** "Mon–Sat, 9:00 AM–5:00 PM" */
export function windowText(w: RecallSettings): string {
  const days = [...w.days].sort();
  const contiguous = days.every((d, i) => i === 0 || d === days[i - 1] + 1);
  const dayText = days.length === 7 ? "Every day"
    : contiguous && days.length > 2 ? `${DAY_NAMES[days[0]]}–${DAY_NAMES[days[days.length - 1]]}`
    : days.map((d) => DAY_NAMES[d]).join(", ");
  return `${dayText}, ${fmt(w.window_start)}–${fmt(w.window_end)}`;
}

function fmt(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}
