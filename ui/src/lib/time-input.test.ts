import { describe, expect, it } from "vitest";
import { isHHMM, joinLocalDateTime, splitLocalDateTime, timeOptions } from "./time-input";

describe("timeOptions", () => {
  it("quarter hours across the day, 24-hour values", () => {
    const o = timeOptions(15);
    expect(o).toHaveLength(96);
    expect(o[0]).toBe("00:00");
    expect(o[1]).toBe("00:15");
    expect(o.at(-1)).toBe("23:45");
  });

  it("half hours", () => {
    const o = timeOptions(30);
    expect(o).toHaveLength(48);
    expect(o).toContain("09:00");
    expect(o).toContain("17:30");
  });

  it("keeps an off-grid existing value, in order", () => {
    const o = timeOptions(30, "09:10");
    expect(o).toHaveLength(49);
    expect(o.indexOf("09:10")).toBe(o.indexOf("09:00") + 1);
    expect(timeOptions(30, "09:00")).toHaveLength(48);
    expect(timeOptions(30, "bogus")).toHaveLength(48);
  });
});

describe("local date-time split/join", () => {
  it("round-trips the datetime-local format", () => {
    expect(splitLocalDateTime("2026-10-19T14:30")).toEqual({ date: "2026-10-19", time: "14:30" });
    expect(joinLocalDateTime("2026-10-19", "14:30")).toBe("2026-10-19T14:30");
  });

  it("blank or partial", () => {
    expect(splitLocalDateTime("")).toEqual({ date: "", time: "" });
    expect(splitLocalDateTime(undefined)).toEqual({ date: "", time: "" });
    expect(splitLocalDateTime("2026-10-19")).toEqual({ date: "2026-10-19", time: "" });
    expect(joinLocalDateTime("", "14:30")).toBe("");
    expect(joinLocalDateTime("2026-10-19", "")).toBe("");
  });

  it("validates HH:MM", () => {
    expect(isHHMM("00:00")).toBe(true);
    expect(isHHMM("23:59")).toBe(true);
    expect(isHHMM("24:00")).toBe(false);
    expect(isHHMM("9:00")).toBe(false);
  });
});
