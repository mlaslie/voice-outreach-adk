import { describe, expect, it } from "vitest";
import samplePreview from "./ehr-export-sample.json";
import {
  EXAMPLE_PROBLEMS,
  EXAMPLE_PROFILE,
  SAMPLE_PREVIEW,
  SAMPLE_SIMULATION,
  demoPreview,
  demoSimulate,
  demoValidate,
  quote,
} from "./ehr-export-demo";
import { getIn, setCode, setDocumentEnabled, setItemType, setLinkId, toggleSend } from "./ehr-export";

// The sample-mode stub is held to the real engine's output for the example
// (scripts/ehr_export_ui_fixtures.py), so the demo shows what the API would.

describe("demoPreview matches the engine on the example", () => {
  const p = demoPreview(EXAMPLE_PROFILE);
  it("produces the same bundle", () => {
    expect(p.bundle).toEqual(SAMPLE_PREVIEW.bundle);
    expect(p.skipped).toEqual(SAMPLE_PREVIEW.skipped);
    expect(p.ok).toBe(true);
  });
  it("produces the engine's source map", () => {
    expect(p.source_map).toEqual(SAMPLE_PREVIEW.source_map);
    expect((samplePreview as { source_map: unknown[] }).source_map.length).toBeGreaterThan(0);
  });
  it("every source-map pointer exists on its side", () => {
    for (const l of p.source_map) {
      if (l.source !== null) expect(getIn(p.source_record, l.source.split("/").slice(1)), l.source).not.toBeUndefined();
      expect(getIn(p.bundle, l.target.split("/").slice(1)), l.target).not.toBeUndefined();
    }
  });
});

describe("demoPreview follows the mapper's edits", () => {
  it("a code map edit changes the coded answer", () => {
    const edited = setCode(EXAMPLE_PROFILE, 0, "pain_trend", "worse", "code", "deteriorating");
    const qr = getIn(demoPreview(edited).bundle, ["entry", 0, "resource"]) as { item: { linkId: string; answer: { valueCoding?: { code: string } }[] }[] };
    expect(qr.item.find((i) => i.linkId === "2.3")!.answer[0].valueCoding!.code).toBe("deteriorating");
  });
  it("an excluded question disappears and is listed; an untypeable one is reported skipped", () => {
    const off = demoPreview(toggleSend(EXAMPLE_PROFILE, 0, "pain_location", false));
    const items = getIn(off.bundle, ["entry", 0, "resource", "item"]) as { linkId: string }[];
    expect(items.some((i) => i.linkId === "2.2")).toBe(false);
    expect(off.skipped).toEqual([{ question_id: "pain_location", reason: "excluded by the profile", source: "/questionnaire_response/item/3/answer" }]);
    const p = demoPreview(setCode(EXAMPLE_PROFILE, 0, "pain_trend", "worse", "code", ""));
    expect(p.skipped).toEqual([{ question_id: "pain_trend", reason: "could not be typed (coding)", source: "/questionnaire_response/item/4/answer" }]);
  });
  it("a changed type and linkId show at once", () => {
    let e = setItemType(EXAMPLE_PROFILE, 0, "pain_severity", "string");
    e = setLinkId(e, 0, "pain_severity", "9.9");
    const items = getIn(demoPreview(e).bundle, ["entry", 0, "resource", "item"]) as { linkId: string; answer: unknown[] }[];
    expect(items.find((i) => i.linkId === "9.9")!.answer).toEqual([{ valueString: "8" }]);
  });
  it("the summary document is added only when on", () => {
    expect(demoPreview(setDocumentEnabled(EXAMPLE_PROFILE, 0, "summary", true, false)).bundle).toEqual(SAMPLE_PREVIEW.bundle);
    const on = demoPreview(setDocumentEnabled(EXAMPLE_PROFILE, 0, "summary", true, true));
    expect((getIn(on.bundle, ["entry"]) as unknown[]).length).toBe(5);
  });
});

describe("demoValidate matches profile_problems on the example", () => {
  it("gives the engine's problems for the example", () => {
    expect(demoValidate(EXAMPLE_PROFILE)).toEqual(EXAMPLE_PROBLEMS);
  });
  it("reports a type that does not fit, and a missing code", () => {
    const bad = setItemType(EXAMPLE_PROFILE, 0, "pain_present", "integer");
    expect(demoValidate(bad).some((p) => p.level === "error" && p.path === "use_cases[0].questionnaire_response.items.pain_present")).toBe(true);
    const miss = setCode(EXAMPLE_PROFILE, 0, "pain_trend", "same", "code", "");
    const w = demoValidate(miss).find((p) => p.path.endsWith("items.pain_trend"));
    expect(w?.level).toBe("warning");
    expect(w?.message).toContain("'same'");
  });
  it("refuses duplicate link ids and an unacknowledged transcript", () => {
    const dup = setLinkId(EXAMPLE_PROFILE, 0, "pain_location", "2.1");
    expect(demoValidate(dup).some((p) => p.level === "error" && p.message.includes("link_id '2.1'"))).toBe(true);
  });
});

describe("demoSimulate matches the fake server", () => {
  it("two runs: created then updated, the same stored resources", () => {
    const s = demoSimulate(EXAMPLE_PROFILE, demoPreview(EXAMPLE_PROFILE));
    expect(s.runs).toEqual(SAMPLE_SIMULATION.runs);
    expect(s.resources).toEqual(SAMPLE_SIMULATION.resources);
  });
  it("quote() is Python's quote(safe=':/')", () => {
    expect(quote("https://x.org/mrn|A 1")).toBe("https://x.org/mrn%7CA%201");
  });
});
