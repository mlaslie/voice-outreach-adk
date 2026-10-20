import { describe, expect, it } from "vitest";
import {

  canApprove,
  canDelete,
  canRetire,
  clampMinMatch,
  demoCreate,
  demoEdit,
  demoItems,
  demoSettings,
  demoTry,
  editableCategories,
  filterItems,
  itemDraftError,
  keywordMatch,
  needsReapproval,
  parseAltQuestions,
  settingsPatch,
  sortItems,
  statusCounts,
  toggleCategory,
} from "./knowledge";

const items = demoItems();
const byId = (id: string) => items.find((i) => i.item_id === id)!;

describe("status rules", () => {
  it("deletes drafts and retired entries only", () => {
    expect(canDelete({ status: "draft" })).toBe(true);
    expect(canDelete({ status: "retired" })).toBe(true);
    expect(canDelete({ status: "approved" })).toBe(false);
  });
  it("approves anything not already approved; retires approved only", () => {
    expect(canApprove({ status: "draft" })).toBe(true);
    expect(canApprove({ status: "approved" })).toBe(false);
    expect(canRetire({ status: "approved" })).toBe(true);
    expect(canRetire({ status: "draft" })).toBe(false);
  });
  it("flags a draft that was approved before", () => {
    expect(needsReapproval({ status: "draft", version: 2, approved_at: null })).toBe(true);
    expect(needsReapproval({ status: "draft", version: 1, approved_at: "2026-10-01T00:00:00Z" })).toBe(true);
    expect(needsReapproval({ status: "draft", version: 1, approved_at: null })).toBe(false);
    expect(needsReapproval({ status: "approved", version: 3, approved_at: "x" })).toBe(false);
  });
});

describe("filterItems / sortItems / statusCounts", () => {
  it("filters by status and searches question, phrasings and answer", () => {
    expect(filterItems(items, "approved", "").every((i) => i.status === "approved")).toBe(true);
    expect(filterItems(items, "all", "PARKING").map((i) => i.item_id)).toEqual(["kq_parking"]);
    expect(filterItems(items, "all", "bandage wet").map((i) => i.item_id)).toEqual(["kq_shower"]);
    expect(filterItems(items, "all", "elm street").map((i) => i.item_id)).toEqual(["kq_parking"]);
    expect(filterItems(items, "retired", "parking")).toEqual([]);
  });
  it("puts drafts first, retired last", () => {
    const s = sortItems(items).map((i) => i.status);
    expect(s.indexOf("approved")).toBeGreaterThan(s.lastIndexOf("draft"));
    expect(s[s.length - 1]).toBe("retired");
  });
  it("counts each status", () => {
    expect(statusCounts(items)).toEqual({ all: 6, draft: 2, approved: 3, retired: 1 });
  });
});

describe("editing", () => {
  it("parses one phrasing per line, dropping blanks, repeats and the main question", () => {
    expect(parseAltQuestions(" Can I shower? \n\ncan i shower?\nWhen can I shower?\n  Is it OK  to bathe? ", "When can I shower?"))
      .toEqual(["Can I shower?", "Is it OK to bathe?"]);
  });
  it("validates lengths and the category", () => {
    const ok = { question: "Q?", answer: "A.", category: "activity", alt_questions: [] };
    expect(itemDraftError(ok, ["activity"])).toBeNull();
    expect(itemDraftError({ ...ok, question: " " }, ["activity"])).toMatch(/question/);
    expect(itemDraftError({ ...ok, question: "x".repeat(301) }, ["activity"])).toMatch(/300/);
    expect(itemDraftError({ ...ok, answer: "x".repeat(601) }, ["activity"])).toMatch(/600/);
    expect(itemDraftError({ ...ok, alt_questions: Array(11).fill("a") }, ["activity"])).toMatch(/10/);
    expect(itemDraftError({ ...ok, category: "medication" }, ["activity"])).toMatch(/categor/);
  });
  it("never offers a locked category", () => {
    expect(editableCategories(demoSettings()).map((c) => c.key)).not.toContain("medication");
    expect(editableCategories(demoSettings()).map((c) => c.key)).not.toContain("symptom");
  });
  it("never turns a locked category on", () => {
    expect(toggleCategory(["diet"], "medication", ["medication", "symptom"])).toEqual(["diet"]);
    expect(toggleCategory(["diet"], "activity", ["medication"])).toEqual(["diet", "activity"]);
    expect(toggleCategory(["diet", "activity"], "diet", ["medication"])).toEqual(["activity"]);
  });
  it("sends an edited approved entry back to draft with version + 1 (sample data)", () => {
    const shower = byId("kq_shower");
    const edited = demoEdit(shower, { question: shower.question, answer: "New.", category: "activity", alt_questions: [] });
    expect(edited).toMatchObject({ status: "draft", version: shower.version + 1, answer: "New." });
    expect(needsReapproval(edited)).toBe(true);
    const draft = byId("kq_forms");
    expect(demoEdit(draft, { question: "Q?", answer: "A.", category: "supplies", alt_questions: [] }))
      .toMatchObject({ status: "draft", version: 1, category_label: "Supplies" });
  });
  it("creates sample entries as version 1 drafts", () => {
    expect(demoCreate({ question: " Q? ", answer: "A.", category: "diet", alt_questions: [] }))
      .toMatchObject({ status: "draft", version: 1, question: "Q?", approved_at: null });
  });
});

describe("settings", () => {
  it("clamps the minimum match to 0.5–1", () => {
    expect(clampMinMatch(0.2)).toBe(0.5);
    expect(clampMinMatch(1.4)).toBe(1);
    expect(clampMinMatch(0.851)).toBe(0.85);
    expect(clampMinMatch(Number.NaN)).toBe(0.5);
  });
  it("sends only what changed", () => {
    const saved = demoSettings();
    expect(settingsPatch(saved, { ...saved })).toEqual({});
    expect(settingsPatch(saved, { ...saved, allowed_categories: [...saved.allowed_categories].reverse() })).toEqual({});
    expect(settingsPatch(saved, { ...saved, enabled: true, min_match: 0.9 })).toEqual({ enabled: true, min_match: 0.9 });
    expect(settingsPatch(saved, { ...saved, channels: { voice: false, sms: false } })).toEqual({ channels: { voice: false, sms: false } });
  });
});

describe("demoTry (sample data only)", () => {
  const on = { ...demoSettings(), enabled: true };
  it("answers a matching approved entry word for word", () => {
    const r = demoTry("When can I take a shower?", items, on);
    expect(r).toMatchObject({ outcome: "answered", item_id: "kq_shower", item_version: 2, answer: byId("kq_shower").answer });
    expect(r.note).toBeUndefined();
    expect(demoTry("Where do I park?", items, on)).toMatchObject({ outcome: "answered", item_id: "kq_parking" });
    expect(demoTry("Am I allowed to be driving?", items, on)).toMatchObject({ outcome: "answered", item_id: "kq_drive" });
  });
  it("always defers medication and symptom questions", () => {
    expect(demoTry("Can I take ibuprofen before my shower?", items, on)).toMatchObject({ outcome: "deferred", category: "medication" });
    expect(demoTry("My incision is red, can I shower?", items, on)).toMatchObject({ outcome: "deferred", category: "symptom" });
  });
  it("defers drafts, retired entries, weak matches and switched-off categories", () => {
    expect(demoTry("What can I eat after surgery?", items, on).outcome).toBe("deferred"); // draft
    expect(demoTry("Where can I buy an ice pack?", items, on).outcome).toBe("deferred"); // retired
    expect(demoTry("Who won the game?", items, on)).toMatchObject({ outcome: "deferred", category: "other" });
    const noActivity = { ...on, allowed_categories: on.allowed_categories.filter((k) => k !== "activity") };
    expect(demoTry("When can I shower?", items, noActivity)).toMatchObject({ outcome: "deferred", category: "activity" });
  });
  it("notes that answering is off", () => {
    expect(demoTry("When can I shower?", items, demoSettings()).note).toMatch(/off/);
  });
  it("scores the share of the patient's keywords found", () => {
    expect(keywordMatch("When can I shower?", "Can I take a shower?")).toBe(1);
    expect(keywordMatch("Showering tomorrow?", "When can I shower?")).toBe(0.5);
    expect(keywordMatch("?", "anything")).toBe(0);
  });
});
