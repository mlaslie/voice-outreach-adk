import { describe, expect, it } from "vitest";
import { categoryLabel, formatQuestion, questionPill, questionsFor, questionSummary, sortQuestions } from "./patient-questions";
import { PatientQuestion } from "./types";

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
