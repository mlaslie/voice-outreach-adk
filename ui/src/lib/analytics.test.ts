import { describe, expect, it } from "vitest";
import { minutes, pct, sampleSummary, share, summaryUrl } from "./analytics";

describe("analytics helpers", () => {
  it("formats rates, with a dash when there is no denominator", () => {
    expect(pct(0.844)).toBe("84%");
    expect(pct(null)).toBe("–");
    expect(pct(Number.NaN)).toBe("–");
  });
  it("clamps bar widths and never divides by zero", () => {
    expect(share(5, 10)).toBe(50);
    expect(share(5, 0)).toBe(0);
    expect(share(20, 10)).toBe(100);
  });
  it("formats sign-off durations", () => {
    expect(minutes(45)).toBe("45 min");
    expect(minutes(90)).toBe("1.5 h");
    expect(minutes(60 * 72)).toBe("3 d");
    expect(minutes(null)).toBe("–");
  });
  it("only ever asks for a valid range", () => {
    expect(summaryUrl(0, "all")).toBe("/api/analytics/summary?days=1&channel=all");
    expect(summaryUrl(9999, "sms")).toBe("/api/analytics/summary?days=365&channel=sms");
  });
  it("sample data has a day per bar and no names", () => {
    const s = sampleSummary(7, "all", new Date("2026-10-20T12:00:00Z"));
    expect(s.clinical.tiers_by_day).toHaveLength(7);
    expect(s.range.end_day).toBe("2026-10-20");
    expect(JSON.stringify(s)).not.toMatch(/patient_name|\+1\d{10}/);
  });
});

describe("call outcomes", () => {
  it("rows add up to the total, and texts are noted", async () => {
    const { outcomeRows, notCompletedText, sampleSummary } = await import("./analytics");
    const o = sampleSummary(30, "all").outcomes!;
    const rows = outcomeRows(o);
    expect(rows.reduce((a, r) => a + r.count, 0)).toBe(o.total);
    expect(rows.find((r) => r.key === "voicemail")!.sub).toBe("34 texted");
    expect(notCompletedText({ hung_up: 2, identity_verification_failed: 5 })).toBe("identity not verified 5 · hung up 2");
  });
});
