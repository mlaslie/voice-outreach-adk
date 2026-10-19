import { afterEach, describe, expect, it } from "vitest";
import { setTimeFormat } from "../time";
import { inCallingWindow, isoToZonedInput, nextCallingSlot, windowText, zonedInputToIso } from "./window";
import { RecallSettings } from "../types";

const W: RecallSettings = {
  enabled: true, interval_minutes: 120, window_start: "09:00", window_end: "17:00",
  days: [0, 1, 2, 3, 4, 5], max_recalls: 2,
};
const NY = "America/New_York";

describe("calling hours (clinic time zone)", () => {
  it("accepts a weekday afternoon and refuses after hours and Sunday", () => {
    expect(inCallingWindow("2030-01-07T15:00:00-05:00", W, NY)).toBe(true);   // Mon 3 PM
    expect(inCallingWindow("2030-01-07T17:00:00-05:00", W, NY)).toBe(false);  // end is exclusive
    expect(inCallingWindow("2030-01-07T18:00:00-05:00", W, NY)).toBe(false);  // Mon 6 PM
    expect(inCallingWindow("2030-01-06T15:00:00-05:00", W, NY)).toBe(false);  // Sunday
  });

  it("judges by the clinic's clock, not the browser's", () => {
    // 22:30 UTC is 5:30 PM in New York (closed) but 2:30 PM in Los Angeles.
    expect(inCallingWindow("2030-01-07T22:30:00Z", W, NY)).toBe(false);
    expect(inCallingWindow("2030-01-07T22:30:00Z", W, "America/Los_Angeles")).toBe(true);
  });

  it("finds the next allowed quarter-hour", () => {
    // Mon 6:10 PM -> Tue 9:00 AM; Sat 4:50 PM -> Sat 4:50 is inside? rounds to 5:00 (closed) -> Mon 9:00.
    expect(nextCallingSlot(Date.parse("2030-01-07T18:10:00-05:00"), W, NY)).toBe("2030-01-08T14:00:00.000Z");
    expect(nextCallingSlot(Date.parse("2030-01-12T16:50:00-05:00"), W, NY)).toBe("2030-01-14T14:00:00.000Z");
    expect(nextCallingSlot(Date.parse("2030-01-07T10:05:00-05:00"), W, NY)).toBe("2030-01-07T15:15:00.000Z");
  });

  afterEach(() => setTimeFormat("24h"));

  it("describes the window in plain words, 12-hour", () => {
    setTimeFormat("12h");
    expect(windowText(W)).toBe("Mon–Sat, 9:00 AM–5:00 PM");
    expect(windowText({ ...W, days: [0, 2, 4] })).toBe("Mon, Wed, Fri, 9:00 AM–5:00 PM");
  });

  it("describes the window in plain words, 24-hour", () => {
    setTimeFormat("24h");
    expect(windowText(W)).toBe("Mon–Sat, 09:00–17:00");
    expect(windowText({ ...W, days: [0, 1, 2, 3, 4, 5, 6], window_start: "08:30", window_end: "20:00" })).toBe("Every day, 08:30–20:00");
  });
});

describe("daylight saving (B-8)", () => {
  it("finds Monday 09:00 EST after a Saturday-evening miss on the fall-back weekend", () => {
    // Sat 31 Oct 2026 17:30 EDT = 21:30Z; clocks go back Sun 1 Nov.
    const slot = nextCallingSlot(Date.UTC(2026, 9, 31, 21, 30), W, NY);
    expect(slot && new Date(slot).toISOString()).toBe("2026-11-02T14:00:00.000Z");
  });
  it("finds Sunday 09:00 EST when Sunday is allowed", () => {
    const slot = nextCallingSlot(Date.UTC(2026, 9, 31, 21, 30), { ...W, days: [0, 1, 2, 3, 4, 5, 6] }, NY);
    expect(slot && new Date(slot).toISOString()).toBe("2026-11-01T14:00:00.000Z");
  });
  it("is in hours at 09:00 EDT the Friday before and refuses 08:59", () => {
    expect(inCallingWindow("2026-10-30T13:00:00Z", W, NY)).toBe(true);
    expect(inCallingWindow("2026-10-30T12:59:00Z", W, NY)).toBe(false);
  });
});

describe("time inputs in clinic time (B-19)", () => {
  it("shows a run at 09:00 Eastern as 09:00 whatever the browser's zone", () => {
    expect(isoToZonedInput("2026-10-20T13:00:00Z", NY)).toBe("2026-10-20T09:00");
    expect(isoToZonedInput("2026-11-03T14:00:00Z", NY)).toBe("2026-11-03T09:00");   // EST
  });
  it("reads 09:00 typed as 09:00 Eastern, across the DST change", () => {
    expect(zonedInputToIso("2026-10-20T09:00", NY)).toBe("2026-10-20T13:00:00.000Z");
    expect(zonedInputToIso("2026-11-03T09:00", NY)).toBe("2026-11-03T14:00:00.000Z");
    expect(zonedInputToIso("nonsense", NY)).toBeNull();
  });
  it("round-trips", () => {
    const iso = "2026-10-31T21:15:00.000Z";
    expect(zonedInputToIso(isoToZonedInput(iso, NY), NY)).toBe(iso);
  });
});
