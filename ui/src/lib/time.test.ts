import { afterEach, describe, expect, it } from "vitest";
import { fmtClock, fmtDateTime, fmtHHMM, getTimeFormat, setTimeFormat, splitMeridiem } from "./time";

const UTC = "UTC";
afterEach(() => setTimeFormat("24h"));

describe("time format store", () => {
  it("defaults to 24h and ignores junk", () => {
    expect(getTimeFormat()).toBe("24h");
    setTimeFormat("12h");
    expect(getTimeFormat()).toBe("12h");
    setTimeFormat("bogus" as never);
    expect(getTimeFormat()).toBe("24h");
  });
});

describe("fmtClock", () => {
  it("24h is zero-padded HH:mm", () => {
    setTimeFormat("24h");
    expect(fmtClock("2026-10-19T14:30:00Z", { timeZone: UTC })).toBe("14:30");
    expect(fmtClock("2026-10-19T00:05:00Z", { timeZone: UTC })).toBe("00:05");
    expect(fmtClock("2026-10-19T09:00:00Z", { timeZone: UTC })).toBe("09:00");
    expect(fmtClock("2026-10-19T12:00:00Z", { timeZone: UTC })).toBe("12:00");
  });

  it("12h uses AM/PM with midnight and noon as 12", () => {
    setTimeFormat("12h");
    expect(fmtClock("2026-10-19T14:30:00Z", { timeZone: UTC })).toBe("2:30 PM");
    expect(fmtClock("2026-10-19T00:05:00Z", { timeZone: UTC })).toBe("12:05 AM");
    expect(fmtClock("2026-10-19T12:00:00Z", { timeZone: UTC })).toBe("12:00 PM");
  });

  it("honours a time zone", () => {
    setTimeFormat("24h");
    // 14:30 UTC on Oct 19 is 10:30 EDT.
    expect(fmtClock("2026-10-19T14:30:00Z", { timeZone: "America/New_York" })).toBe("10:30");
    setTimeFormat("12h");
    expect(fmtClock(new Date("2026-10-19T14:30:00Z"), { timeZone: "America/New_York" })).toBe("10:30 AM");
  });

  it("seconds and explicit format override", () => {
    setTimeFormat("12h");
    expect(fmtClock("2026-10-19T14:30:05Z", { timeZone: UTC, seconds: true, format: "24h" })).toBe("14:30:05");
    expect(fmtClock("2026-10-19T14:30:05Z", { timeZone: UTC, seconds: true })).toBe("2:30:05 PM");
  });

  it("dash for missing or invalid", () => {
    expect(fmtClock(undefined)).toBe("—");
    expect(fmtClock("not a date")).toBe("—");
  });
});

describe("fmtDateTime", () => {
  const iso = "2026-10-19T14:30:00Z";
  it("date part plus clock in either format", () => {
    setTimeFormat("24h");
    expect(fmtDateTime(iso, { weekday: "short", month: "short", day: "numeric", timeZone: UTC })).toBe("Mon, Oct 19, 14:30");
    setTimeFormat("12h");
    expect(fmtDateTime(iso, { weekday: "short", month: "short", day: "numeric", timeZone: UTC })).toBe("Mon, Oct 19, 2:30 PM");
  });

  it("custom separator, clock only without date fields", () => {
    setTimeFormat("24h");
    expect(fmtDateTime(iso, { month: "short", day: "numeric", timeZone: UTC, sep: " · " })).toBe("Oct 19 · 14:30");
    expect(fmtDateTime(iso, { timeZone: UTC })).toBe("14:30");
  });

  it("date part follows the time zone too", () => {
    setTimeFormat("24h");
    // 02:00 UTC on Oct 20 is still Oct 19 in New York.
    expect(fmtDateTime("2026-10-20T02:00:00Z", { month: "short", day: "numeric", timeZone: "America/New_York" })).toBe("Oct 19, 22:00");
  });
});

describe("fmtHHMM", () => {
  it("24h", () => {
    setTimeFormat("24h");
    expect(fmtHHMM("14:30")).toBe("14:30");
    expect(fmtHHMM("9:00")).toBe("09:00");
    expect(fmtHHMM("00:05")).toBe("00:05");
  });
  it("12h edges", () => {
    setTimeFormat("12h");
    expect(fmtHHMM("14:30")).toBe("2:30 PM");
    expect(fmtHHMM("00:05")).toBe("12:05 AM");
    expect(fmtHHMM("12:00")).toBe("12:00 PM");
    expect(fmtHHMM("09:00")).toBe("9:00 AM");
  });
  it("passes junk through", () => {
    expect(fmtHHMM("")).toBe("");
    expect(fmtHHMM("soon")).toBe("soon");
  });
});

describe("splitMeridiem", () => {
  it("splits 12h and leaves 24h alone", () => {
    expect(splitMeridiem("2:30 PM")).toEqual({ time: "2:30", meridiem: "PM" });
    expect(splitMeridiem("14:30")).toEqual({ time: "14:30", meridiem: "" });
  });
});
