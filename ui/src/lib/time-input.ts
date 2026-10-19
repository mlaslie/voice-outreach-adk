/**
 * Pure helpers behind ClinicTimeSelect / ClinicDateTimeInput.
 *
 * Native <input type="time"> and "datetime-local" render 12- or 24-hour by
 * the OS locale and cannot be told otherwise, so the console uses a date
 * input plus a time <select> whose labels follow the clinic setting. The
 * values stay 24-hour "HH:MM" and, joined, the datetime-local string
 * "YYYY-MM-DDTHH:MM" — so existing state, validation and the
 * zonedInputToIso / isoToZonedInput helpers are unchanged.
 */

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function isHHMM(v: string): boolean {
  return HHMM.test(v);
}

/** "HH:MM" values across the day every `stepMinutes`, plus `include` when it
 *  is a valid time off that grid (an existing value is never dropped). */
export function timeOptions(stepMinutes = 15, include?: string | null): string[] {
  const step = Math.max(1, Math.min(60 * 24, Math.floor(stepMinutes)));
  const out: string[] = [];
  for (let m = 0; m < 24 * 60; m += step) {
    out.push(`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
  }
  if (include && isHHMM(include) && !out.includes(include)) {
    out.push(include);
    out.sort();
  }
  return out;
}

/** "YYYY-MM-DDTHH:MM" → its parts ("" for a missing part). */
export function splitLocalDateTime(v: string | null | undefined): { date: string; time: string } {
  const m = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?/.exec((v ?? "").trim());
  if (!m) return { date: "", time: "" };
  return { date: m[1], time: m[2] ?? "" };
}

/** Parts → "YYYY-MM-DDTHH:MM", or "" until both are present. */
export function joinLocalDateTime(date: string, time: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !isHHMM(time)) return "";
  return `${date}T${time}`;
}
