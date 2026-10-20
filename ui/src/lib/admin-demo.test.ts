import { afterEach, describe, expect, it, vi } from "vitest";
import { demoAdminReads, demoAdminSessions, demoAudit, demoCallEvents } from "./admin-demo";
import { demoData } from "./nurse/demo";
import { buildTimeline, SAFE_DETAIL_KEYS } from "./diagnostics";

describe("admin sample data", () => {
  const now = new Date("2026-10-20T15:00:00Z");
  const data = demoData(now);
  const sessions = demoAdminSessions(data, now);

  afterEach(() => vi.unstubAllGlobals());

  it("reads never touch the network", async () => {
    const fetchSpy = vi.fn(() => Promise.reject(new Error("no network in sample mode")));
    vi.stubGlobal("fetch", fetchSpy);
    const reads = demoAdminReads(sessions, data.calls, now);
    const s = sessions[0];
    expect((await reads.messages(s.phone, s.call_sid)).length).toBeGreaterThan(0);
    expect((await reads.audit()).length).toBeGreaterThan(0);
    await reads.callEvents(data.calls[0].call_sid!);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("has open, finished and opted-out conversations", () => {
    const states = new Set(sessions.map((s) => s.state));
    expect(states).toContain("awaiting_choice");
    expect(states).toContain("survey");
    expect(states).toContain("opted_out");
  });

  it("gives a voice call a timeline and a text-only call none", () => {
    const voice = data.calls.find((c) => c.call_status === "success" && c.channel !== "sms")!;
    const sms = data.calls.find((c) => c.channel === "sms")!;
    const v = demoCallEvents(voice);
    expect(v.available).toBe(true);
    expect(v.summary.tool_calls).toBeGreaterThan(0);
    expect(v.summary.first_audio_ms).toBeGreaterThan(0);
    expect(buildTimeline(v.timeline).length).toBeGreaterThan(0);
    expect(demoCallEvents(sms).available).toBe(false);
  });

  it("shows an error on an interrupted sample call", () => {
    const c = data.calls.find((x) => x.call_status === "interrupted")!;
    expect(demoCallEvents(c).summary.errors).toBe(1);
  });

  it("sample events carry only safe detail keys", () => {
    for (const c of data.calls) {
      for (const e of demoCallEvents(c).timeline) {
        for (const k of Object.keys(e)) {
          if (k !== "ms" && k !== "event") expect(SAFE_DETAIL_KEYS as readonly string[]).toContain(k);
        }
      }
    }
  });

  it("lists audit entries newest first", () => {
    const at = demoAudit(now).map((e) => e.at);
    expect([...at].sort().reverse()).toEqual(at);
  });
});
