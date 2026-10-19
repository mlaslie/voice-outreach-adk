import { describe, expect, it } from "vitest";
import type { CallDoc, FollowUpReason } from "./types";
import {
  FOLLOW_UP_REASON_PRESETS,
  agentOriginalReasons,
  applyOverrideLocally,
  buildOverrideBody,
  initialReasonSelection,
  normaliseReasonTexts,
  nurseReasonsMeta,
  overrideProblem,
  reasonTierLabel,
  reasonsChanged,
  reasonsFor,
  reasonsSetByNurse,
  sortReasons,
  summariseReasons,
} from "./follow-up-reasons";

function call(over: Partial<CallDoc> = {}): CallDoc {
  return {
    doc_id: "CA1",
    to: "+15550100",
    from: "+15550199",
    initiated_at: "2026-10-19T14:00:00Z",
    updated_at: "2026-10-19T14:05:00Z",
    ...over,
  };
}

const MED: FollowUpReason = { tier: "urgent_same_day", text: "question asked (medication)", source: "triage" };
const SLEEP: FollowUpReason = { tier: "informational", text: "not sleeping well", source: "triage" };
const SWELL: FollowUpReason = { tier: "routine_next_business_day", text: "leg swelling", source: "triage" };

describe("sortReasons", () => {
  it("puts the most urgent first and keeps order within a tier", () => {
    const a = { tier: "informational", text: "a" };
    const b = { tier: "informational", text: "b" };
    const out = sortReasons([a, SLEEP, MED, b, { tier: "emergency", text: "chest pain" }, SWELL]);
    expect(out.map((r) => r.text)).toEqual(["chest pain", MED.text, SWELL.text, "a", SLEEP.text, "b"]);
  });

  it("sorts unknown tiers last without throwing", () => {
    expect(sortReasons([{ tier: "weird", text: "x" }, SLEEP]).map((r) => r.text)).toEqual([SLEEP.text, "x"]);
  });
});

describe("reasonsFor", () => {
  it("returns the list, sorted, when the call has one", () => {
    expect(reasonsFor(call({ follow_up_reasons: [SLEEP, MED] }))).toEqual([MED, SLEEP]);
  });

  it("falls back to the sentence at the call's tier on old calls", () => {
    expect(reasonsFor(call({ follow_up: "urgent_same_day", follow_up_reason: "Pain 8/10." }))).toEqual([
      { tier: "urgent_same_day", text: "Pain 8/10.", source: "agent" },
    ]);
  });

  it("puts an old override's sentence at the agent's tier", () => {
    const c = call({
      follow_up: "none", agent_follow_up: "urgent_same_day", follow_up_reason: "Pain 8/10.",
      follow_up_override: { at: "2026-10-19T15:00:00Z", from: "urgent_same_day", to: "none", reason: "Called back" },
    });
    expect(reasonsFor(c)[0].tier).toBe("urgent_same_day");
  });

  it("is empty with nothing recorded, and ignores blank entries", () => {
    expect(reasonsFor(call())).toEqual([]);
    expect(reasonsFor(undefined)).toEqual([]);
    expect(reasonsFor(call({ follow_up_reasons: [{ tier: "none", text: "  " }], follow_up_reason: "Fine." }))[0].text).toBe("Fine.");
  });
});

describe("nurse-set reasons", () => {
  const nurse = call({
    follow_up: "routine_next_business_day",
    agent_follow_up: "urgent_same_day",
    follow_up_reasons: [{ tier: "routine_next_business_day", text: "Medication question", source: "nurse", set_by: "R. Nurse", at: "2026-10-19T16:00:00Z" }],
    agent_follow_up_reasons: [SLEEP, MED],
    agent_follow_up_reason: "Patient reports question asked (medication).",
  });

  it("knows a nurse set them, who and when", () => {
    expect(reasonsSetByNurse(nurse)).toBe(true);
    expect(nurseReasonsMeta(nurse)).toEqual({ by: "R. Nurse", at: "2026-10-19T16:00:00Z" });
    expect(reasonsSetByNurse(call({ follow_up_reasons: [MED] }))).toBe(false);
    expect(nurseReasonsMeta(call({ follow_up_reasons: [MED] }))).toBeNull();
  });

  it("returns the agent's originals, sorted", () => {
    expect(agentOriginalReasons(nurse)).toEqual([MED, SLEEP]);
  });

  it("falls back to the agent's kept sentence when the agent had no list", () => {
    const c = { ...nurse, agent_follow_up_reasons: [] };
    expect(agentOriginalReasons(c)).toEqual([{ tier: "urgent_same_day", text: "Patient reports question asked (medication).", source: "agent" }]);
  });

  it("has no originals when no nurse replaced the list", () => {
    expect(agentOriginalReasons(call({ follow_up_reasons: [MED] }))).toEqual([]);
  });
});

describe("summariseReasons", () => {
  it("shows the top reason plus a count, with the rest in the tooltip", () => {
    const s = summariseReasons([SLEEP, MED, SWELL]);
    expect(s.top).toEqual(MED);
    expect(s.label).toBe("question asked (medication) +2 more");
    expect(s.tooltip).toBe("Next day: leg swelling\nNoted: not sleeping well");
  });

  it("is just the text for one reason, and empty for none", () => {
    expect(summariseReasons([MED]).label).toBe(MED.text);
    expect(summariseReasons([MED]).tooltip).toBe("");
    expect(summariseReasons([])).toEqual({ top: null, rest: [], label: "", tooltip: "" });
  });
});

describe("the change dialog", () => {
  it("has the preset list", () => {
    expect(FOLLOW_UP_REASON_PRESETS).toContain("Medication question");
    expect(FOLLOW_UP_REASON_PRESETS).toHaveLength(11);
  });

  it("normalises whitespace and drops duplicates", () => {
    expect(normaliseReasonTexts(["  Pain ", "pain", "", "Fever   high"])).toEqual(["Pain", "Fever high"]);
  });

  it("prefills from the call's current reasons", () => {
    expect(initialReasonSelection(call({ follow_up_reasons: [SLEEP, MED] }))).toEqual([MED.text, SLEEP.text]);
    expect(initialReasonSelection(call({ follow_up_reason: "x".repeat(201) }))).toEqual([]);
  });

  it("requires a reason, at most ten, and a real note when one is typed", () => {
    expect(overrideProblem([], "")).toMatch(/at least one/);
    expect(overrideProblem(["  "], "")).toMatch(/at least one/);
    expect(overrideProblem(Array.from({ length: 11 }, (_, i) => `r${i}`), "")).toMatch(/At most 10/);
    expect(overrideProblem(["x".repeat(201)], "")).toMatch(/200/);
    expect(overrideProblem(["Pain"], "ok")).toMatch(/note/);
    expect(overrideProblem(["Pain"], "")).toBeNull();
    expect(overrideProblem(["Pain"], "Called back")).toBeNull();
  });

  it("sends reasons, and the note as reason only when given", () => {
    expect(buildOverrideBody("informational", [" Pain ", "pain", "Fever"], "  ")).toEqual({ follow_up: "informational", reasons: ["Pain", "Fever"] });
    expect(buildOverrideBody("none", ["Pain"], " Called back. ")).toEqual({ follow_up: "none", reasons: ["Pain"], reason: "Called back." });
  });

  it("detects a changed selection regardless of order and case", () => {
    const c = call({ follow_up_reasons: [MED, SLEEP] });
    expect(reasonsChanged(c, [SLEEP.text, MED.text.toUpperCase()])).toBe(false);
    expect(reasonsChanged(c, [MED.text])).toBe(true);
  });
});

describe("applyOverrideLocally", () => {
  const now = "2026-10-19T17:00:00Z";

  it("mirrors the backend: nurse reasons, sentence, originals kept, history", () => {
    const c = call({ follow_up: "urgent_same_day", follow_up_reasons: [MED, SLEEP], follow_up_reason: "Patient reports question asked (medication)." });
    const out = applyOverrideLocally(c, { follow_up: "routine_next_business_day", reasons: ["Medication refill", "Not sleeping well"] }, "you", now);
    expect(out.follow_up).toBe("routine_next_business_day");
    expect(out.agent_follow_up).toBe("urgent_same_day");
    expect(out.follow_up_reason).toBe("Nurse: Medication refill; Not sleeping well.");
    expect(out.follow_up_reasons).toEqual([
      { tier: "routine_next_business_day", text: "Medication refill", source: "nurse", set_by: "you", at: now },
      { tier: "routine_next_business_day", text: "Not sleeping well", source: "nurse", set_by: "you", at: now },
    ]);
    expect(out.agent_follow_up_reasons).toEqual([MED, SLEEP]);
    expect(out.agent_follow_up_reason).toBe("Patient reports question asked (medication).");
    expect(out.follow_up_history).toHaveLength(1);
    expect(out.follow_up_override).toMatchObject({ from: "urgent_same_day", to: "routine_next_business_day", reason: "Medication refill; Not sleeping well", reasons: ["Medication refill", "Not sleeping well"] });
    expect(reasonTierLabel(out.follow_up)).toBe("Next day");
  });

  it("keeps the agent's originals on a second change", () => {
    const c = call({ follow_up: "urgent_same_day", follow_up_reasons: [MED] });
    const once = applyOverrideLocally(c, { follow_up: "informational", reasons: ["Pain"] }, "you", now);
    const twice = applyOverrideLocally(once, { follow_up: "none", reasons: ["Discussed with patient — resolved"], reason: "Called back" }, "you", now);
    expect(twice.agent_follow_up).toBe("urgent_same_day");
    expect(twice.agent_follow_up_reasons).toEqual([MED]);
    expect(twice.follow_up_override?.reason).toBe("Called back");
    expect(twice.follow_up_history).toHaveLength(2);
  });
});

describe("sample data", () => {
  it("has mixed-tier reasons, an old sentence-only call and a nurse-changed call", async () => {
    const { demoData } = await import("./nurse/demo");
    const calls = demoData(new Date("2026-10-19T15:00:00Z")).calls;
    const by = (name: string) => calls.find((c) => c.patient_name === name)!;
    expect(reasonsFor(by("Priya Raman")).map((r) => r.tier)).toEqual(["urgent_same_day", "urgent_same_day", "informational"]);
    expect(by("Dolores Whitfield").follow_up_reasons).toBeUndefined();
    expect(reasonsFor(by("Dolores Whitfield"))).toHaveLength(1);
    const harold = by("Harold Okafor");
    expect(nurseReasonsMeta(harold)?.by).toBe("J. Moreno, RN");
    expect(agentOriginalReasons(harold)).toHaveLength(1);
  });
});
