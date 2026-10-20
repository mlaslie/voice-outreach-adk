import { describe, expect, it } from "vitest";
import { buildTimeline, formatMs, formatOffset, originMs, safeDetail } from "./diagnostics";
import { CallEvent } from "./types";

const TL: CallEvent[] = [
  { ms: 40, event: "prompt.rendered" },
  { ms: 120, event: "media.start" },
  { ms: 960, event: "agent.audio_start" },
  { ms: 5000, event: "tool.call", name: "verify_identity" },
  { ms: 5000, event: "tool.call", name: "lookup" },
  { ms: 5300, event: "tool.response", name: "lookup", status: "ok" },
  { ms: 5420, event: "tool.response", name: "verify_identity", status: "verified" },
  { ms: 7000, event: "agent.interrupted" },
  { ms: 8000, event: "live.error", code: "1011" },
  { ms: 9000, event: "tool.call", name: "save_report" },
  { ms: 9500, event: "call.ended", reason: "hangup" },
];

describe("buildTimeline", () => {
  const rows = buildTimeline(TL);

  it("counts offsets from media start", () => {
    expect(originMs(TL)).toBe(120);
    expect(rows[0]).toMatchObject({ event: "prompt.rendered", offsetMs: -80 });
    expect(rows.find((r) => r.event === "agent.audio_start")?.offsetMs).toBe(840);
  });

  it("groups each tool call with its response by name", () => {
    const tools = rows.filter((r) => r.kind === "tool");
    expect(tools.map((r) => r.tool)).toEqual([
      { name: "verify_identity", status: "verified", durationMs: 420 },
      { name: "lookup", status: "ok", durationMs: 300 },
      { name: "save_report", status: null, durationMs: null },
    ]);
    expect(rows.some((r) => r.event === "tool.response")).toBe(false);
  });

  it("marks live.* events as errors", () => {
    const err = rows.find((r) => r.event === "live.error");
    expect(err?.kind).toBe("error");
    expect(err?.detail).toBe("code: 1011");
  });

  it("keeps an unmatched tool response as its own row", () => {
    const r = buildTimeline([{ ms: 0, event: "media.start" }, { ms: 10, event: "tool.response", name: "x", status: "error" }]);
    expect(r[1]).toMatchObject({ label: "Tool response: x", detail: "status: error" });
  });

  it("falls back to the first event without media.start, and handles an empty timeline", () => {
    expect(buildTimeline([{ ms: 500, event: "media.no_start" }])[0].offsetMs).toBe(0);
    expect(buildTimeline([])).toEqual([]);
  });
});

describe("safeDetail", () => {
  it("never shows keys outside the safe list (no answers, no transcript text)", () => {
    const e: CallEvent = { ms: 1, event: "tool.call", name: "save_report", text: "I have chest pain", answers: { pain: 9 }, status: "ok" };
    expect(safeDetail(e)).toBe("name: save_report · status: ok");
  });

  it("drops non-primitive values even under a safe key", () => {
    expect(safeDetail({ ms: 1, event: "x", reason: { nested: "y" } })).toBe("");
  });
});

describe("formatting", () => {
  it("formats offsets", () => {
    expect(formatOffset(1234)).toBe("+1,234 ms");
    expect(formatOffset(0)).toBe("+0 ms");
    expect(formatOffset(-80)).toBe("−80 ms");
  });
  it("formats tile values", () => {
    expect(formatMs(840)).toBe("840 ms");
    expect(formatMs(2400)).toBe("2.4 s");
    expect(formatMs(null)).toBe("—");
  });
});
