import { describe, expect, it } from "vitest";
import {
  answerSource, categoryLabel, formatQuestion, questionEntries, questionHandling, questionPill, questionsFor, questionSummary,
  reviewFromResponse, shadowStats, sortQuestions, withShadowReview,
} from "./patient-questions";
import { CallDoc, PatientQuestion } from "./types";

const sr = (survey_responses: Record<string, unknown> | undefined) => ({ survey_responses });

const med: PatientQuestion = { text: "Can I take ibuprofen with my pain pills?", category: "medication", priority: "urgent_same_day" };
const appt: PatientQuestion = { text: "When is my follow-up appointment?", category: "appointment", priority: "routine_next_business_day" };
const drive: PatientQuestion = { text: "When can I drive again?", category: "activity", priority: "routine_next_business_day" };

describe("sortQuestions", () => {
  it("puts immediate first and keeps the patient's order otherwise", () => {
    expect(sortQuestions([appt, med, drive])).toEqual([med, appt, drive]);
    expect(sortQuestions([drive, appt])).toEqual([drive, appt]);
  });
});

describe("questionsFor", () => {
  it("is empty for old calls", () => {
    expect(questionsFor(sr(undefined))).toEqual([]);
    expect(questionsFor(sr({ pain_present: "no" }))).toEqual([]);
  });
  it("drops malformed items and treats an unknown priority as immediate", () => {
    const qs = questionsFor(sr({
      patient_questions: [null, "text", { text: "  " }, { text: "Is this normal?", category: "symptom", priority: "weird" }, appt],
    }));
    expect(qs).toEqual([
      { text: "Is this normal?", category: "symptom", priority: "urgent_same_day" },
      appt,
    ]);
  });
  it("defaults a missing category to other", () => {
    expect(questionsFor(sr({ patient_questions: [{ text: "Hi?", priority: "routine_next_business_day" }] }))[0].category)
      .toBe("other");
  });
});

describe("questionSummary", () => {
  it("flags yes with none captured", () => {
    expect(questionSummary(sr({ has_questions: "yes", patient_questions: [] }))).toMatchObject({ noneCaptured: true, count: 0 });
    expect(questionSummary(sr({ has_questions: "yes" })).noneCaptured).toBe(true);
  });
  it("no questions and never asked are distinct", () => {
    expect(questionSummary(sr({ has_questions: "no" })).hasQuestions).toBe("no");
    expect(questionSummary(sr({})).hasQuestions).toBeUndefined();
  });
});

describe("questionPill", () => {
  it("is null when there is nothing to flag", () => {
    expect(questionPill(sr({}))).toBeNull();
    expect(questionPill(sr({ has_questions: "no" }))).toBeNull();
  });
  it("counts questions and uses the immediate tone if any is immediate", () => {
    expect(questionPill(sr({ has_questions: "yes", patient_questions: [appt, med] }))).toMatchObject({ label: "2 questions", tone: "urgent" });
  });
  it("routine tone and singular label for one routine question", () => {
    expect(questionPill(sr({ has_questions: "yes", patient_questions: [drive] }))).toMatchObject({ label: "1 question", tone: "routine" });
  });
  it("shows a pill when the patient has questions but none were captured", () => {
    expect(questionPill(sr({ has_questions: "yes", patient_questions: [] }))).toMatchObject({ label: "Has questions", tone: "routine" });
  });
  it("hover text lists the questions immediate first", () => {
    expect(questionPill(sr({ patient_questions: [appt, med] }))!.title.split("\n")[0]).toMatch(/^Immediate · Medication: /);
  });
});

describe("labels", () => {
  it("maps categories to nurse labels", () => {
    expect(categoryLabel("paperwork_billing")).toBe("Paperwork or billing");
    expect(categoryLabel("symptom")).toBe("Symptom or wound");
    expect(categoryLabel("recovery")).toBe("Recovery timeline");
    expect(categoryLabel(undefined)).toBe("Other");
  });
  it("formats a question for plain lists", () => {
    expect(formatQuestion(drive)).toBe("When can I drive again? (Activity, Next business day)");
  });
});

describe("knowledge-base fields (answered by the agent)", () => {
  const shower = {
    text: "When can I shower?", category: "activity", priority: "routine_next_business_day",
    outcome: "answered", item_id: "kq_shower", item_version: 3, answer: "  You can shower after 48 hours.  ", satisfied: true,
  };
  it("keeps an older question to exactly its three fields", () => {
    expect(Object.keys(questionsFor(sr({ patient_questions: [appt] }))[0]).sort()).toEqual(["category", "priority", "text"]);
  });
  it("reads the optional fields and drops malformed ones", () => {
    const [q] = questionsFor(sr({ patient_questions: [shower] }));
    expect(q).toMatchObject({ outcome: "answered", item_id: "kq_shower", item_version: 3, answer: "You can shower after 48 hours.", satisfied: true });
    const [bad] = questionsFor(sr({ patient_questions: [{ ...shower, outcome: "maybe", item_version: "3", satisfied: "no", answer: 5 }] }));
    expect(bad.outcome).toBeUndefined();
    expect(bad.item_version).toBeUndefined();
    expect(bad.satisfied).toBeUndefined();
    expect(bad.answer).toBeUndefined();
    expect(questionsFor(sr({ patient_questions: [{ ...shower, satisfied: null }] }))[0].satisfied).toBeNull();
  });
  it("classifies who dealt with each question", () => {
    expect(questionHandling({})).toBe("nurse");
    expect(questionHandling({ outcome: "deferred" })).toBe("nurse");
    expect(questionHandling({ outcome: "answered", satisfied: true })).toBe("answered");
    expect(questionHandling({ outcome: "answered", satisfied: null })).toBe("answered");
    expect(questionHandling({ outcome: "answered" })).toBe("answered");
    expect(questionHandling({ outcome: "answered", satisfied: false })).toBe("unsatisfied");
  });
  it("names the source", () => {
    expect(answerSource({ item_version: 3 })).toBe("Clinic Q&A v3");
    expect(answerSource({})).toBe("Clinic Q&A");
  });
  it("counts what is left for a nurse", () => {
    const s = questionSummary(sr({ patient_questions: [shower, { ...shower, text: "Can I drive?", satisfied: false }, med] }));
    expect(s).toMatchObject({ count: 3, answered: 1, forNurse: 2, immediate: 1 });
  });
  it("shows an all-answered pill, and counts only nurse questions otherwise", () => {
    expect(questionPill(sr({ patient_questions: [shower] }))).toMatchObject({ label: "1 answered", tone: "good" });
    const p = questionPill(sr({ patient_questions: [shower, appt] }))!;
    expect(p).toMatchObject({ label: "1 question", tone: "routine" });
    expect(p.title).toContain("(answered by the agent)");
  });
  it("is unchanged for older questions", () => {
    expect(questionPill(sr({ patient_questions: [appt, drive] }))).toMatchObject({ label: "2 questions", tone: "routine" });
    expect(formatQuestion(appt)).toBe("When is my follow-up appointment? (Appointment, Next business day)");
  });
});

describe("knowledge v2 fields", () => {
  const shadow = {
    outcome: "answered", reason: "answered", answer: " Lift nothing over 10 lb. ", support_score: 0.91, at: "2026-10-20T10:00:00Z",
    sources: [{ kind: "guideline", id: "kg_knee", title: "Lifting", version: 2 }, { kind: "weird", id: "kq_1", title: "Q" }, null, { kind: "example" }],
  };
  const raw = [
    { text: "Can I lift my grandkid?", category: "activity", priority: "routine_next_business_day", shadow,
      shadow_review: { verdict: "not_right", note: "  Too vague. ", by: "rn@example.org", at: "2026-10-20T11:00:00Z" } },
    { text: "Can I take ibuprofen?", category: "medication", priority: "urgent_same_day",
      shadow: { outcome: "maybe" }, shadow_review: { verdict: "meh" } },
    { text: "Where do I park?", category: "appointment", priority: "routine_next_business_day", outcome: "answered",
      answer: "Level 2.", sources: [{ kind: "example", id: "kq_parking", title: "Where do I park?", version: 1 }], support_score: 0.97 },
  ];

  it("keeps each question's position on the call, after sorting", () => {
    const es = questionEntries(sr({ patient_questions: [null, ...raw] }));
    expect(es.map((e) => [e.q.text, e.index])).toEqual([
      ["Can I take ibuprofen?", 2],
      ["Can I lift my grandkid?", 1],
      ["Where do I park?", 3],
    ]);
  });
  it("reads shadow drafts, reviews and live sources; drops malformed ones", () => {
    const [med, lift, park] = questionsFor(sr({ patient_questions: raw }));
    expect(lift.shadow).toEqual({
      outcome: "answered", reason: "answered", answer: "Lift nothing over 10 lb.", support_score: 0.91, at: "2026-10-20T10:00:00Z",
      sources: [{ kind: "guideline", id: "kg_knee", title: "Lifting", version: 2 }, { kind: "example", id: "kq_1", title: "Q" }],
    });
    expect(lift.shadow_review).toEqual({ verdict: "not_right", note: "Too vague.", by: "rn@example.org", at: "2026-10-20T11:00:00Z" });
    expect(med.shadow).toBeUndefined();
    expect(med.shadow_review).toBeUndefined();
    expect(park).toMatchObject({ outcome: "answered", support_score: 0.97, sources: [{ id: "kq_parking", version: 1 }] });
  });
  it("leaves old questions exactly as they were", () => {
    expect(questionsFor(sr({ patient_questions: [appt] }))).toEqual([appt]);
  });
  it("reads the rating from whatever the route returns", () => {
    const fb = { verdict: "good" as const, note: null, by: null, at: "t" };
    const rv = { verdict: "not_right", note: "x", by: "a@example.org", at: "t2" };
    expect(reviewFromResponse(rv, 0, fb)).toEqual(rv);
    expect(reviewFromResponse({ shadow_review: rv }, 0, fb)).toEqual(rv);
    expect(reviewFromResponse({ question: { shadow_review: rv } }, 0, fb)).toEqual(rv);
    expect(reviewFromResponse({ survey_responses: { patient_questions: [{}, { shadow_review: rv }] } }, 1, fb)).toEqual(rv);
    expect(reviewFromResponse({ ok: true }, 0, fb)).toEqual(fb);
    expect(reviewFromResponse(null, 0, fb)).toEqual(fb);
  });
  it("sets one question's rating on a copy of the call", () => {
    const call = { doc_id: "c1", survey_responses: { patient_questions: raw } } as unknown as CallDoc;
    const review = { verdict: "good" as const, note: null, by: "you", at: "t" };
    const next = withShadowReview(call, 2, review);
    expect((next.survey_responses!.patient_questions as unknown as Record<string, unknown>[])[2].shadow_review).toEqual(review);
    expect((call.survey_responses!.patient_questions as unknown as Record<string, unknown>[])[2]).not.toHaveProperty("shadow_review");
    expect(withShadowReview(call, 9, review)).toBe(call);
  });
  it("counts shadow drafts and ratings across calls", () => {
    expect(shadowStats([sr({ patient_questions: raw }), sr({}), sr({ patient_questions: [{ ...raw[0], shadow_review: { verdict: "good" } }] })]))
      .toEqual({ drafts: 2, rated: 2, good: 1, notRight: 1 });
  });
});
