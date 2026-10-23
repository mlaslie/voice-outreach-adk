import { describe, expect, it } from "vitest";
import {
  EhrProfile,
  EhrQuestion,
  SourceLink,
  addObservation,
  addUseCase,
  advancedFields,
  canRequeue,
  codeMapRows,
  counterparts,
  errorsByTarget,
  excludedOf,
  getIn,
  insertOrdered,
  itemOf,
  jsonDiff,
  missingCodes,
  normalizePreview,
  problemsUnder,
  readablePath,
  removeObservation,
  setCode,
  setDocumentEnabled,
  setIn,
  setItemType,
  setLinkId,
  setResourceEnabled,
  skippedBySource,
  targetBadges,
  toggleSend,
  typeOptions,
  unmapItem,
  uploadBody,
} from "./ehr-export";
import { EXAMPLE_PROFILE, SAMPLE_PREVIEW } from "./ehr-export-demo";

const P = EXAMPLE_PROFILE;
const TREND: EhrQuestion = { id: "pain_trend", ask: "Is it getting better?", type: "choice", choices: ["better", "same", "worse"], allowed_types: ["coding", "string"] };
const ORDER = ["overall_feeling", "pain_present", "pain_severity", "pain_location", "pain_trend"];

describe("setIn", () => {
  it("copies only the path and leaves the original alone", () => {
    const a = { x: { y: 1 }, z: { w: 2 } };
    const b = setIn(a, ["x", "y"], 5);
    expect(b).toEqual({ x: { y: 5 }, z: { w: 2 } });
    expect(a.x.y).toBe(1);
    expect(b.z).toBe(a.z);
  });
  it("removes with undefined, and creates missing containers", () => {
    expect(setIn({ a: 1, b: 2 }, ["a"], undefined)).toEqual({ b: 2 });
    expect(setIn({ l: [1, 2, 3] }, ["l", 1], undefined)).toEqual({ l: [1, 3] });
    expect(setIn({}, ["a", 0, "b"], 1)).toEqual({ a: [{ b: 1 }] });
  });
});

describe("profile editor reducers", () => {
  it("sets a linkId on a mapped question, keeping every other field", () => {
    const out = setLinkId(P, 0, "pain_trend", "2.3a");
    expect(itemOf(out, 0, "pain_trend")).toEqual({ ...itemOf(P, 0, "pain_trend"), link_id: "2.3a" });
    expect(itemOf(P, 0, "pain_trend")!.link_id).toBe("2.3");
  });
  it("maps an unmapped question as a string item, in spec order", () => {
    const base = unmapItem(P, 0, "pain_location");
    expect(itemOf(base, 0, "pain_location")).toBeNull();
    const out = setLinkId(base, 0, "pain_location", "2.2", ORDER);
    expect(itemOf(out, 0, "pain_location")).toEqual({ link_id: "2.2", type: "string" });
    const keys = Object.keys(getIn(out, ["use_cases", 0, "questionnaire_response", "items"]) as object);
    expect(keys.indexOf("pain_location")).toBe(keys.indexOf("pain_severity") + 1);
  });
  it("creates the QuestionnaireResponse block for a new use case", () => {
    const p: EhrProfile = { use_cases: [{ campaign_id: "pre_op" }] };
    expect(getIn(setLinkId(p, 0, "ride_home", "1"), ["use_cases", 0, "questionnaire_response", "items", "ride_home"])).toEqual({ link_id: "1", type: "string" });
  });
  it("edits one code-map cell; an emptied entry is removed", () => {
    const out = setCode(P, 0, "pain_trend", "same", "display", "No change");
    expect(getIn(out, ["use_cases", 0, "questionnaire_response", "items", "pain_trend", "codes", "same"])).toMatchObject({ code: "unchanged", display: "No change" });
    let gone = setCode(P, 0, "pain_trend", "same", "code", "");
    gone = setCode(gone, 0, "pain_trend", "same", "display", "");
    expect(getIn(gone, ["use_cases", 0, "questionnaire_response", "items", "pain_trend", "codes", "same"])).toBeUndefined();
    const sys = setCode(P, 0, "pain_trend", "worse", "system", "https://x.example/cs");
    expect(getIn(sys, ["use_cases", 0, "questionnaire_response", "items", "pain_trend", "codes", "worse", "system"])).toBe("https://x.example/cs");
  });
  it("a new display alone starts the entry with an empty code (shown as missing)", () => {
    const p = setCode(unmapItem(P, 0, "pain_trend"), 0, "pain_trend", "better", "display", "Better");
    expect(getIn(p, ["use_cases", 0, "questionnaire_response", "items", "pain_trend", "codes", "better"])).toEqual({ display: "Better", code: "" });
  });
  it("toggles Send through exclude, without duplicates", () => {
    const off = toggleSend(P, 0, "overall_feeling", false);
    expect(excludedOf(off, 0)).toEqual(["overall_feeling"]);
    expect(excludedOf(toggleSend(off, 0, "overall_feeling", false), 0)).toEqual(["overall_feeling"]);
    expect(excludedOf(toggleSend(off, 0, "overall_feeling", true), 0)).toEqual([]);
    // still mapped: excluding never drops the mapping
    expect(itemOf(off, 0, "overall_feeling")).not.toBeNull();
  });
  it("changing the type drops the keys the new type may not carry", () => {
    const s = itemOf(setItemType(P, 0, "pain_trend", "string"), 0, "pain_trend")!;
    expect([s.link_id, s.type, s.codes, s.system]).toEqual(["2.3", "string", undefined, undefined]);
    const c = itemOf(setItemType(P, 0, "pain_location", "coding"), 0, "pain_location")!;
    expect([c.link_id, c.type, c.codes, c.unit]).toEqual(["2.2", "coding", {}, undefined]);
    const q = setItemType(setIn(P, ["use_cases", 0, "questionnaire_response", "items", "pain_severity", "unit"], "{score}"), 0, "pain_severity", "integer");
    expect(itemOf(q, 0, "pain_severity")!.unit).toBeUndefined();
  });
  it("turns resources and documents on and off; documents need the acknowledgement", () => {
    const off = setResourceEnabled(P, 0, "task", false);
    expect(getIn(off, ["use_cases", 0, "task", "enabled"])).toBe(false);
    expect(getIn(off, ["use_cases", 0, "task", "priority"])).toEqual(getIn(P, ["use_cases", 0, "task", "priority"]));
    expect(setDocumentEnabled(P, 0, "transcript", true, false)).toBe(P);
    const on = setDocumentEnabled(P, 0, "transcript", true, true);
    expect(getIn(on, ["use_cases", 0, "transcript"])).toMatchObject({ enabled: true, phi_acknowledged: true, language: "original" });
    expect(getIn(setDocumentEnabled(on, 0, "transcript", false, false), ["use_cases", 0, "transcript", "phi_acknowledged"])).toBe(true);
  });
  it("adds and removes observations and use cases", () => {
    const a = addObservation(P, 0, "pain_trend");
    expect((getIn(a, ["use_cases", 0, "observations"]) as unknown[]).length).toBe(3);
    expect(getIn(removeObservation(a, 0, 0), ["use_cases", 0, "observations", 0, "source"])).toBe("fever_reported");
    const u = addUseCase(P, "pre_op");
    expect(getIn(u, ["use_cases", 1, "campaign_id"])).toBe("pre_op");
  });
  it("insertOrdered places by order and appends unknown keys", () => {
    expect(Object.keys(insertOrdered({ a: 1, c: 3 }, "b", 2, ["a", "b", "c"]))).toEqual(["a", "b", "c"]);
    expect(Object.keys(insertOrdered({ a: 1 }, "zz", 2, ["a"]))).toEqual(["a", "zz"]);
  });
});

describe("questions table helpers", () => {
  it("offers only fitting types, widened by a value_map", () => {
    expect(typeOptions(TREND, itemOf(P, 0, "pain_trend"))).toEqual(["string", "coding"]);
    expect(typeOptions(TREND, { type: "coding", value_map: { worse: 1 } })).toEqual(["boolean", "integer", "decimal", "string", "coding"]);
  });
  it("finds missing codes and lists the grid rows", () => {
    expect(missingCodes(TREND, itemOf(P, 0, "pain_trend"))).toEqual([]);
    const p = setCode(P, 0, "pain_trend", "same", "code", "");
    expect(missingCodes(TREND, itemOf(p, 0, "pain_trend"))).toEqual(["same"]);
    expect(codeMapRows(TREND, { type: "coding", codes: { worse: { code: "w" }, extra: { code: "e" } } })).toEqual(["better", "same", "worse", "extra"]);
  });
  it("finds a row's problems in either path style", () => {
    const probs = [
      { level: "error" as const, path: "use_cases.0.questionnaire_response.items.pain_trend.codes", message: "a" },
      { level: "warning" as const, path: "use_cases[0].questionnaire_response.items.pain_trend", message: "b" },
      { level: "warning" as const, path: "use_cases[0].questionnaire_response.items.pain_trend_x", message: "c" },
    ];
    expect(problemsUnder(probs, "/use_cases/0/questionnaire_response/items/pain_trend").map((p) => p.message)).toEqual(["a", "b"]);
  });
  it("lists fields the mapper does not edit", () => {
    expect(advancedFields(P)).toEqual([]);
    const p = setIn(setIn(P, ["use_cases", 0, "questionnaire_response", "items", "pain_trend", "value_map"], { meh: "same" }), ["x_custom"], 1);
    expect(advancedFields(p)).toEqual(["x_custom", "use_cases[0].questionnaire_response.items.pain_trend.value_map"]);
  });
});

describe("source-map highlight resolution", () => {
  const MAP: SourceLink[] = [
    { source: "/questionnaire_response/item/4/answer", target: "/entry/0/resource/item/3/answer/0", question_id: "pain_trend" },
    { source: "/questionnaire_response/item/2/answer", target: "/entry/0/resource/item/2/answer/0", question_id: "pain_severity" },
    { source: "/questionnaire_response/item/2/answer", target: "/entry/2/resource/valueInteger", question_id: "pain_severity" },
    { source: "/disposition/follow_up", target: "/entry/1/resource/priority" },
  ];
  it("an exact source finds every target (one answer, two resources)", () => {
    expect(counterparts(MAP, "source", "/questionnaire_response/item/2/answer").pointers).toEqual(["/entry/0/resource/item/2/answer/0", "/entry/2/resource/valueInteger"]);
  });
  it("a leaf inside a mapped target resolves to its nearest mapped ancestor", () => {
    const r = counterparts(MAP, "target", "/entry/0/resource/item/3/answer/0/valueCoding/code");
    expect(r.pointers).toEqual(["/questionnaire_response/item/4/answer"]);
    expect(r.own).toEqual(["/entry/0/resource/item/3/answer/0"]);
  });
  it("a container finds everything mapped inside it", () => {
    expect(counterparts(MAP, "source", "/questionnaire_response/item/4").pointers).toEqual(["/entry/0/resource/item/3/answer/0"]);
    expect(counterparts(MAP, "target", "/entry/1").pointers).toEqual(["/disposition/follow_up"]);
  });
  it("an unmapped node finds nothing", () => {
    expect(counterparts(MAP, "source", "/encounter/channel").pointers).toEqual([]);
  });
  it("works on the sample: pain_trend both ways", () => {
    const left = counterparts(SAMPLE_PREVIEW.source_map, "source", "/questionnaire_response/item/4/answer");
    expect(left.pointers).toEqual(["/entry/0/resource/item/4/answer/0/valueCoding"]);
    expect(counterparts(SAMPLE_PREVIEW.source_map, "source", "/questionnaire_response/item/4").pointers).toEqual(["/entry/0/resource/item/4"]);
    const right = counterparts(SAMPLE_PREVIEW.source_map, "target", "/entry/0/resource/item/4/answer/0/valueCoding/code");
    expect(right.pointers).toEqual(["/questionnaire_response/item/4/answer"]);
    expect(counterparts(SAMPLE_PREVIEW.source_map, "target", "/entry/0/resource/item/4").pointers).toEqual(["/questionnaire_response/item/4"]);
  });
  it("never links a value from outside the record, but badges it with its note", () => {
    const m: SourceLink[] = [{ source: null, target: "/entry/4/resource/content/0/attachment/data", note: "transcript (original)" }];
    expect(counterparts(m, "target", "/entry/4/resource/content/0/attachment/data").pointers).toEqual([]);
    expect(counterparts(m, "source", "").pointers).toEqual([]);
    expect(targetBadges(m).get("/entry/4/resource/content/0/attachment/data")).toBe("transcript (original)");
  });
  it("badges name the question, else the source field", () => {
    const b = targetBadges(MAP);
    expect(b.get("/entry/0/resource/item/3/answer/0")).toBe("pain_trend");
    expect(b.get("/entry/1/resource/priority")).toBe("follow_up");
  });
  it("skipped and error flags by pointer", () => {
    expect(skippedBySource([{ question_id: "q", reason: "not mapped", source: "/questionnaire_response/item/9" }]).get("/questionnaire_response/item/9")).toBe("not mapped");
    const bundle = { entry: [{ resource: { item: [{}] } }] };
    const e = errorsByTarget([{ path: "entry[0].resource.item[0].answer", message: "bad" }, { message: "no path" }], bundle);
    expect([...e.entries()]).toEqual([["/entry/0/resource/item/0", "bad"]]);
  });
});

describe("normalizePreview", () => {
  it("reads the engine's own shape too", () => {
    const n = normalizePreview({
      source_record: { questionnaire_response: { item: [{ linkId: "a" }, { linkId: "b" }] } },
      bundle: {},
      skipped: [{ question: "b", reason: "not mapped" }],
      errors: ["Bundle.entry: bad"],
    });
    expect(n.skipped).toEqual([{ question_id: "b", reason: "not mapped", source: "/questionnaire_response/item/1/answer" }]);
    expect(normalizePreview({ skipped: [{ question_id: "c", reason: "r", source: null }], source_map: [{ source: null, target: "/x" }] })).toMatchObject({
      skipped: [{ question_id: "c", reason: "r", source: null }],
      source_map: [{ source: null, target: "/x" }],
    });
    expect(n.errors).toEqual([{ message: "Bundle.entry: bad" }]);
    expect(n.ok).toBe(false);
    expect(n.source_map).toEqual([]);
  });
});

describe("jsonDiff (Activate)", () => {
  it("is empty for equal documents", () => {
    expect(jsonDiff(P, structuredClone(P))).toEqual([]);
  });
  it("reports changed, added and removed leaves with readable paths", () => {
    let b = setCode(P, 0, "pain_trend", "same", "code", "stable");
    b = setIn(b, ["use_cases", 0, "task", "description"], undefined);
    b = setIn(b, ["validate_output"], false);
    b = setIn(b, ["use_cases", 0, "questionnaire_response", "exclude"], ["overall_feeling"]);
    const d = jsonDiff(P, b);
    expect(d).toHaveLength(4);
    expect(d).toContainEqual({ path: "/validate_output", kind: "changed", before: true, after: false });
    expect(d).toContainEqual({ path: "/use_cases/0/questionnaire_response/exclude/0", kind: "added", after: "overall_feeling" });
    expect(d).toContainEqual({ path: "/use_cases/0/questionnaire_response/items/pain_trend/codes/same/code", kind: "changed", before: "unchanged", after: "stable" });
    expect(d).toContainEqual({ path: "/use_cases/0/task/description", kind: "removed", before: "RN follow-up after automated post-op outreach" });
    expect(readablePath("/use_cases/0/questionnaire_response/items/pain_trend/codes/same/code")).toBe("use_cases[0].questionnaire_response.items.pain_trend.codes.same.code");
  });
  it("against nothing, the whole profile is added", () => {
    expect(jsonDiff(undefined, { a: 1 })).toEqual([{ path: "", kind: "added", after: { a: 1 } }]);
  });
});

describe("misc", () => {
  it("uploads JSON as a profile and anything else as YAML", () => {
    expect(uploadBody('{"id": "x"}')).toEqual({ profile: { id: "x" } });
    expect(uploadBody("id: x\n")).toEqual({ yaml: "id: x\n" });
    expect(uploadBody("{not json")).toEqual({ yaml: "{not json" });
  });
  it("requeues only failed and skipped records", () => {
    expect(["failed", "skipped", "exported", "pending_export"].map(canRequeue)).toEqual([true, true, false, false]);
  });
});
