import { describe, expect, it } from "vitest";
import {
  APPROVED_CHARS_BUDGET,
  KnowledgeDoc,
  addProcedures,
  appliesToText,
  approvalOverBudget,
  approvedChars,
  canApprove,
  canDelete,
  canRetire,
  clampMinSupport,
  classificationSummary,
  demoCreate,
  demoCreateDoc,
  demoDocs,
  demoEdit,
  demoEditDoc,
  demoItems,
  demoSettings,
  demoTry,
  docApplies,
  docDraftError,
  guidelineFileError,
  guidelineTextError,
  liveWarnings,
  normaliseFileText,
  normaliseSettings,
  reasonText,
  REASONS,
  shortReason,
  scoreText,
  sourceLabel,
  titleFromFileName,
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
  it("clamps the minimum support to 0.5–1", () => {
    expect(clampMinSupport(0.2)).toBe(0.5);
    expect(clampMinSupport(1.4)).toBe(1);
    expect(clampMinSupport(0.851)).toBe(0.85);
    expect(clampMinSupport(Number.NaN)).toBe(0.5);
  });
  it("sends only what changed", () => {
    const saved = demoSettings();
    expect(settingsPatch(saved, { ...saved })).toEqual({});
    expect(settingsPatch(saved, { ...saved, allowed_categories: [...saved.allowed_categories].reverse() })).toEqual({});
    expect(settingsPatch(saved, { ...saved, mode: "live", min_support: 0.9 })).toEqual({ mode: "live", min_support: 0.9 });
    expect(settingsPatch(saved, { ...saved, channels: { voice: false, sms: false } })).toEqual({ channels: { voice: false, sms: false } });
  });
  it("reads a step-1 document: enabled means live, min_support defaults", () => {
    expect(normaliseSettings({ enabled: true, min_match: 0.8 })).toMatchObject({ mode: "live", min_support: 0.85 });
    expect(normaliseSettings({ enabled: false })).toMatchObject({ mode: "off", locked_categories: ["medication", "symptom"] });
    expect(normaliseSettings({ mode: "shadow", enabled: true, min_support: 0.7 })).toMatchObject({ mode: "shadow", min_support: 0.7 });
    expect(normaliseSettings({ mode: "bogus" }).mode).toBe("off");
    expect(normaliseSettings(null)).toMatchObject({ mode: "off", channels: { voice: true, sms: false } });
  });
  it("warns before going live without approved knowledge or ratings", () => {
    expect(liveWarnings({ approvedItems: 2, approvedDocs: 1, ratings: 30 })).toEqual([]);
    expect(liveWarnings({ approvedItems: 0, approvedDocs: 0, ratings: 30 })[0]).toMatch(/Nothing is approved/);
    expect(liveWarnings({ approvedItems: 0, approvedDocs: 1, ratings: 0 })[0]).toMatch(/not rated/);
    expect(liveWarnings({ approvedItems: 1, approvedDocs: 0, ratings: null })[0]).toMatch(/could not be counted/);
    expect(liveWarnings({ approvedItems: 0, approvedDocs: 0, ratings: 0 })).toHaveLength(2);
  });
});

describe("reasonText", () => {
  it("has plain words for every reason in the contract", () => {
    for (const r of REASONS) {
      const t = reasonText(r);
      expect(t, r).toMatch(/^[A-Z].*\.$/);
      expect(t, r).not.toContain("_");
    }
  });
  it("names the topic and the scores where it can", () => {
    expect(reasonText("category_not_allowed", { category_label: "Diet" })).toBe("Diet questions are switched off in the settings.");
    expect(reasonText("category_not_allowed")).toMatch(/this topic/);
    expect(reasonText("low_support", { support_score: 0.72, min_support: 0.85 })).toMatch(/support 72%, minimum 85%/);
    expect(reasonText("low_support", { min_support: 0.85 })).toMatch(/\(minimum 85%\)/);
    expect(reasonText("low_support")).toBe("The draft answer was not fully backed by the approved material.");
    expect(reasonText("locked_category")).toMatch(/never answered/);
  });
  it("has a short phrase for each reason, for the nurse drawer", () => {
    for (const r of REASONS) expect(shortReason(r), r).toMatch(/^[a-z]/);
    expect(shortReason("not_covered")).toBe("the approved material does not cover it");
    expect(shortReason("new_reason")).toBe("new reason");
    expect(shortReason(null)).toBe("no reason was given");
  });
  it("shows an unknown reason as words, never blank", () => {
    expect(reasonText("budget_exceeded")).toBe("Budget exceeded.");
    expect(reasonText(undefined)).toBe("No reason was given.");
  });
});

describe("classificationSummary", () => {
  const base = { category: "diet", category_label: "Diet", llm_category: "diet", keyword_category: "diet", red_flag: false, vetoed_by: null };
  it("summarises agreement without a veto", () => {
    expect(classificationSummary(base)).toEqual({ category: "Diet", llm: "Diet", keywords: "Diet", agree: true, redFlag: false, veto: null });
  });
  it("says which side blocked and why", () => {
    expect(classificationSummary({ ...base, category: "medication", category_label: "Medication", keyword_category: "medication", vetoed_by: "keywords" }))
      .toMatchObject({ llm: "Diet", keywords: "Medication", agree: false, veto: "Blocked by keyword rules: medication" });
    expect(classificationSummary({ ...base, llm_category: "symptom", keyword_category: "other", vetoed_by: "llm" }).veto)
      .toBe("Blocked by the AI classifier: symptom or wound");
    expect(classificationSummary({ ...base, keyword_category: "other", red_flag: true, vetoed_by: "keywords" }))
      .toMatchObject({ redFlag: true, veto: "Blocked by keyword rules: possible red flag" });
  });
  it("shows a missing side as a dash", () => {
    expect(classificationSummary({ ...base, llm_category: null, keyword_category: null })).toMatchObject({ llm: "—", keywords: "—", agree: false });
  });
});

describe("scores and sources", () => {
  it("formats scores", () => {
    expect(scoreText(0.934)).toBe("93%");
    expect(scoreText(null)).toBeNull();
    expect(scoreText(Number.NaN)).toBeNull();
  });
  it("labels a source with kind, title and version", () => {
    expect(sourceLabel({ kind: "guideline", id: "g1", title: "Lifting", version: 2 })).toBe("Guideline · Lifting · v2");
    expect(sourceLabel({ kind: "example", id: "kq_1", title: "" })).toBe("Example · kq_1");
  });
});

describe("guideline files", () => {
  it("accepts .txt and .md only, and not too big", () => {
    expect(guidelineFileError("Knee lifting.TXT", 2_000)).toBeNull();
    expect(guidelineFileError("discharge.md", 2_000)).toBeNull();
    expect(guidelineFileError("discharge.markdown", 2_000)).toBeNull();
    expect(guidelineFileError("discharge.pdf", 2_000)).toMatch(/\.txt.*\.md/);
    expect(guidelineFileError("discharge.docx", 2_000)).toMatch(/Only plain text/);
    expect(guidelineFileError("notes.md.exe", 2_000)).toMatch(/Only plain text/);
    expect(guidelineFileError("empty.txt", 0)).toMatch(/empty/);
    expect(guidelineFileError("huge.txt", 80_001)).toMatch(/too big/);
  });
  it("checks the text: empty, binary, over 20,000 characters", () => {
    expect(guidelineTextError("Lift nothing over 10 lb.")).toBeNull();
    expect(guidelineTextError("x".repeat(20_000))).toBeNull();
    expect(guidelineTextError("x".repeat(20_001))).toMatch(/20,001 characters.*20,000/);
    expect(guidelineTextError("  \n ")).toMatch(/empty/);
    expect(guidelineTextError("PK\u0000\u0003")).toMatch(/not plain text/);
  });
  it("tidies the text and suggests a title", () => {
    expect(normaliseFileText("\ufeffa\r\nb\rc")).toBe("a\nb\nc");
    expect(titleFromFileName("knee-arthroscopy_lifting.md")).toBe("Knee arthroscopy lifting");
    expect(titleFromFileName(".md")).toBe("");
  });
});

describe("guidelines", () => {
  const docs = demoDocs();
  const doc = (id: string) => docs.find((d) => d.doc_id === id)!;
  const ok = { title: "T", body: "Body.", procedures: [], care_settings: [] };
  it("validates a draft", () => {
    expect(docDraftError(ok)).toBeNull();
    expect(docDraftError({ ...ok, title: " " })).toMatch(/title/);
    expect(docDraftError({ ...ok, title: "x".repeat(121) })).toMatch(/120/);
    expect(docDraftError({ ...ok, body: "" })).toMatch(/text/);
    expect(docDraftError({ ...ok, body: "x".repeat(20_001) })).toMatch(/20,000/);
    expect(docDraftError({ ...ok, procedures: Array.from({ length: 21 }, (_, i) => `p${i}`) })).toMatch(/20 procedures/);
    expect(docDraftError({ ...ok, care_settings: ["home" as never] })).toMatch(/inpatient/);
  });
  it("adds procedure tags without repeats", () => {
    expect(addProcedures(["Knee arthroscopy"], " knee ARTHROSCOPY, hip  replacement\n\n,ACL repair ")).toEqual(
      ["Knee arthroscopy", "hip replacement", "ACL repair"]);
    expect(addProcedures([], " , ")).toEqual([]);
  });
  it("matches procedures either way, and care settings exactly", () => {
    const knee = doc("kg_knee_lifting"); // knee arthroscopy, outpatient
    expect(docApplies(knee, { procedure: "Right knee arthroscopy", care_setting: "outpatient" })).toBe(true);
    expect(docApplies(knee, { procedure: "knee", care_setting: "outpatient" })).toBe(true);
    expect(docApplies(knee, { procedure: "knee arthroscopy", care_setting: "inpatient" })).toBe(false);
    expect(docApplies(knee, { procedure: "knee arthroscopy" })).toBe(false);
    expect(docApplies(knee, { care_setting: "outpatient" })).toBe(false);
    expect(docApplies(doc("kg_bathing"), {})).toBe(true);
    expect(appliesToText(knee)).toBe("knee arthroscopy · Outpatient");
    expect(appliesToText(doc("kg_bathing"))).toBe("Everyone");
  });
  it("counts approved characters against the budget", () => {
    const approved = docs.filter((d) => d.status === "approved").reduce((n, d) => n + d.chars, 0);
    expect(approvedChars(docs)).toBe(approved);
    const big: KnowledgeDoc = { ...doc("kg_inpatient_visitors"), chars: APPROVED_CHARS_BUDGET - approved + 1 };
    expect(approvalOverBudget(big, docs)).toBe(true);
    expect(approvalOverBudget({ ...big, chars: APPROVED_CHARS_BUDGET - approved }, docs)).toBe(false);
    // Re-approving an approved one does not count it twice.
    expect(approvalOverBudget(doc("kg_knee_lifting"), docs)).toBe(false);
  });
  it("edits an approved guideline back to draft with version + 1 (sample data)", () => {
    const knee = doc("kg_knee_lifting");
    const edited = demoEditDoc(knee, { title: knee.title, body: "New text.", procedures: [], care_settings: [] });
    expect(edited).toMatchObject({ status: "draft", version: knee.version + 1, chars: 9 });
    expect(needsReapproval(edited)).toBe(true);
    expect(demoCreateDoc({ ...ok, title: " T " })).toMatchObject({ status: "draft", version: 1, title: "T", chars: 5 });
  });
});

describe("demoTry (sample data only)", () => {
  const live = { ...demoSettings(), mode: "live" as const };
  const docs = demoDocs();
  it("answers from an approved example, with its source and a support score", () => {
    const r = demoTry("When can I take a shower?", items, docs, live);
    expect(r).toMatchObject({ outcome: "answered", reason: "answered", answer: byId("kq_shower").answer });
    expect(r.sources).toEqual([{ kind: "example", id: "kq_shower", title: "When can I shower?", version: 2 }]);
    expect(r.support_score).toBeGreaterThanOrEqual(live.min_support);
    expect(r.classification).toMatchObject({ llm_category: "activity", vetoed_by: null });
  });
  it("answers from a guideline only when it applies to the patient", () => {
    const q = "How heavy can I lift after my knee arthroscopy?";
    const ctx = { procedure: "Knee arthroscopy", care_setting: "outpatient" as const };
    const r = demoTry(q, [], docs, live, ctx);
    expect(r.outcome).toBe("answered");
    expect(r.sources?.[0]).toMatchObject({ kind: "guideline", id: "kg_knee_lifting" });
    expect(demoTry(q, [], docs, live, { ...ctx, care_setting: "inpatient" }).reason).not.toBe("answered");
  });
  it("lets the keyword rules veto what the classifier would allow", () => {
    const r = demoTry("Can I take ibuprofen before my shower?", items, docs, live);
    expect(r).toMatchObject({ outcome: "deferred", reason: "locked_category" });
    expect(r.classification).toMatchObject({ llm_category: "activity", keyword_category: "medication", vetoed_by: "keywords" });
    expect(demoTry("I have chest pain when I walk", items, docs, live)).toMatchObject({ reason: "red_flag" });
  });
  it("defers unclear, switched-off and uncovered questions", () => {
    expect(demoTry("Who won the game?", items, docs, live).reason).toBe("unclassified");
    const noActivity = { ...live, allowed_categories: live.allowed_categories.filter((k) => k !== "activity") };
    expect(demoTry("When can I shower?", items, docs, noActivity).reason).toBe("category_not_allowed");
    expect(demoTry("Can I have coffee?", items, [], live).reason).toBe("not_covered");
    expect(demoTry("Can I have coffee?", [], [], live).reason).toBe("no_knowledge");
    expect(demoTry("  ", items, docs, live).reason).toBe("empty_question");
  });
  it("notes when the mode is not live", () => {
    expect(demoTry("When can I shower?", items, docs, demoSettings()).note).toMatch(/mode is shadow/);
  });
  it("scores the share of the patient's keywords found", () => {
    expect(keywordMatch("When can I shower?", "Can I take a shower?")).toBe(1);
    expect(keywordMatch("Showering tomorrow?", "When can I shower?")).toBe(0.5);
    expect(keywordMatch("?", "anything")).toBe(0);
  });
});
