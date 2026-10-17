import { describe, expect, it } from "vitest";
import { inCallingWindow, nextCallingSlot, windowText } from "./window";
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

  it("describes the window in plain words", () => {
    expect(windowText(W)).toBe("Mon–Sat, 9:00 AM–5:00 PM");
    expect(windowText({ ...W, days: [0, 2, 4] })).toBe("Mon, Wed, Fri, 9:00 AM–5:00 PM");
  });
});
