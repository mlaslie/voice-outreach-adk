import { describe, expect, it } from "vitest";
import { findingsFor, verdictFor, countLevels, summarize } from "./triage";
import { CallDoc } from "./types";

function call(over: Partial<CallDoc> = {}): CallDoc {
  return {
    doc_id: "CA1",
    to: "+12025550123",
    from: "+12025550111",
    initiated_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T10:05:00Z",
    call_status: "success",
    outcome: "questionnaire_completed",
    ...over,
  };
}
const find = (c: CallDoc, key: string) => findingsFor(c).find((f) => f.key === key);

describe("pain", () => {
  it("8+ is an alert", () => {
    const f = find(call({ survey_responses: { pain_present: "yes", pain_severity: 8 } }), "pain");
    expect(f?.level).toBe("alert");
    expect(f?.value).toBe("8 / 10");
  });

  it("4-7 is a watch", () => {
    expect(find(call({ survey_responses: { pain_present: "yes", pain_severity: 5 } }), "pain")?.level)
      .toBe("watch");
  });

  it("low pain is normal", () => {
    expect(find(call({ survey_responses: { pain_present: "yes", pain_severity: 2 } }), "pain")?.level)
      .toBe("normal");
  });

  it("a worsening trend outranks a mild number", () => {
    const f = find(
      call({ survey_responses: { pain_present: "yes", pain_severity: 3, pain_trend: "worse" } }),
      "pain"
    );
    expect(f?.level).toBe("alert");
  });

  it("no pain reads as normal, not missing", () => {
    const f = find(call({ survey_responses: { pain_present: "no" } }), "pain");
    expect(f?.level).toBe("normal");
    expect(f?.value).toBe("None reported");
  });

  it("severe words with no number still alert", () => {
    const f = find(
      call({ survey_responses: { pain_present: "yes", pain_severity_note: "wouldn't say, it's unbearable" } }),
      "pain"
    );
    expect(f?.level).toBe("alert");
  });

  it("a refused number without severe words is unknown, never invented", () => {
    const f = find(
      call({ survey_responses: { pain_present: "yes", pain_severity_note: "wouldn't give a number" } }),
      "pain"
    );
    expect(f?.level).toBe("unknown");
    expect(f?.value).toBe("No number given");
  });

  it("surfaces location and trend as detail", () => {
    const f = find(
      call({ survey_responses: { pain_present: "yes", pain_severity: 5, pain_location: "the incision", pain_trend: "better" } }),
      "pain"
    );
    expect(f?.detail).toContain("the incision");
    expect(f?.detail).toContain("better");
  });
});

describe("unasked vs answered-no", () => {
  it("distinguishes a question never asked from a negative answer", () => {
    const asked = find(call({ survey_responses: { fever_reported: "no" } }), "fever");
    const never = find(call({ survey_responses: {} }), "fever");
    expect(asked?.level).toBe("normal");
    expect(asked?.value).toBe("No");
    expect(never?.level).toBe("unasked");
    expect(never?.value).toBe("Not asked");
  });
});

describe("fever / incision / blood sugar", () => {
  it("fever yes alerts", () => {
    expect(find(call({ survey_responses: { fever_reported: "yes" } }), "fever")?.level).toBe("alert");
  });
  it("incision concern alerts and carries the description", () => {
    const f = find(
      call({ survey_responses: { incision_concerns: "yes", incision_description: "spreading redness" } }),
      "incision"
    );
    expect(f?.level).toBe("alert");
    expect(f?.detail).toBe("spreading redness");
  });
  it("out-of-range blood sugar alerts", () => {
    expect(find(call({ survey_responses: { blood_sugar_status: "high" } }), "sugar")?.level).toBe("alert");
  });
  it("blood sugar is omitted for non-diabetics who weren't asked", () => {
    expect(find(call({ patient_context: { is_diabetic: false }, survey_responses: {} }), "sugar")).toBeUndefined();
  });
  it("blood sugar is shown as unasked for a diabetic who wasn't asked", () => {
    expect(find(call({ patient_context: { is_diabetic: true }, survey_responses: {} }), "sugar")?.level)
      .toBe("unasked");
  });
});

describe("identity", () => {
  it("a DOB mismatch alerts", () => {
    expect(find(call({ dob_verification_result: "mismatch" }), "dob")?.level).toBe("alert");
  });
  it("verified is normal", () => {
    expect(find(call({ dob_verification_result: "verified" }), "dob")?.level).toBe("normal");
  });
});

describe("verdict", () => {
  it("emergency", () => {
    const v = verdictFor(call({ follow_up: "emergency", follow_up_reason: "Chest pain." }));
    expect(v.tone).toBe("emergency");
    expect(v.actionRequired).toBe(true);
    expect(v.reason).toBe("Chest pain.");
  });

  it("urgent requires action", () => {
    expect(verdictFor(call({ follow_up: "urgent_same_day" })).actionRequired).toBe(true);
  });

  it("routine requires action", () => {
    expect(verdictFor(call({ follow_up: "routine_next_business_day" })).actionRequired).toBe(true);
  });

  it("none is clear and needs nothing", () => {
    const v = verdictFor(call({ follow_up: "none" }));
    expect(v.tone).toBe("clear");
    expect(v.actionRequired).toBe(false);
    expect(v.headline).toBe("No action required");
  });

  it("informational needs nothing", () => {
    expect(verdictFor(call({ follow_up: "informational" })).actionRequired).toBe(false);
  });

  it("a voicemail is NOT reported as clear — nothing was collected", () => {
    const v = verdictFor(call({ outcome: "voicemail_left", follow_up: "none" }));
    expect(v.tone).toBe("incomplete");
    expect(v.headline).toBe("No answers collected");
  });

  it("wrong number is incomplete, not clear", () => {
    expect(verdictFor(call({ outcome: "unable_to_reach_wrong_person", follow_up: "none" })).tone)
      .toBe("incomplete");
  });

  it("an in-flight call reads as in progress", () => {
    const v = verdictFor(call({ call_status: undefined, outcome: undefined, follow_up: undefined }));
    expect(v.headline).toBe("Call in progress");
  });

  it("a finished call with no follow-up is flagged, not silently cleared", () => {
    const v = verdictFor(call({ follow_up: undefined }));
    expect(v.headline).toBe("No follow-up recorded");
    expect(v.tone).toBe("incomplete");
  });
});

describe("countLevels", () => {
  it("counts what needs attention", () => {
    const c = countLevels(
      findingsFor(
        call({
          dob_verification_result: "verified",
          survey_responses: {
            pain_present: "yes", pain_severity: 9,
            fever_reported: "yes",
            incision_concerns: "no",
          },
        })
      )
    );
    expect(c.alert).toBe(2);   // pain + fever
    expect(c.normal).toBe(2);  // incision + identity
  });
});

describe("summarize", () => {
  it("never says 'nothing abnormal' when questions went unanswered", () => {
    // Real shape from production: follow_up=urgent_same_day, survey_responses null.
    const s = summarize(findingsFor(call({
      follow_up: "urgent_same_day",
      dob_verification_result: "verified",
      survey_responses: undefined,
    })));
    // Made before 19 Oct 2026: the newer questions don't count as unanswered.
    expect(s?.text).toBe("3 not answered");
    expect(s?.tone).toBe("muted");
  });

  it("leads with alerts", () => {
    const s = summarize(findingsFor(call({
      survey_responses: { pain_present: "yes", pain_severity: 9, fever_reported: "yes", incision_concerns: "no" },
    })));
    expect(s).toEqual({ text: "2 need attention", tone: "alert" });
  });

  it("singular wording for one alert", () => {
    const s = summarize(findingsFor(call({
      survey_responses: { pain_present: "no", fever_reported: "yes", incision_concerns: "no" },
    })));
    expect(s?.text).toBe("1 needs attention");
  });

  it("watch when nothing is acute", () => {
    const s = summarize(findingsFor(call({
      survey_responses: { pain_present: "yes", pain_severity: 5, fever_reported: "no", incision_concerns: "no" },
    })));
    expect(s).toEqual({ text: "1 to note", tone: "watch" });
  });

  it("clears only when everything asked came back normal", () => {
    const s = summarize(findingsFor(call({
      dob_verification_result: "verified",
      survey_responses: { pain_present: "no", fever_reported: "no", incision_concerns: "no" },
    })));
    expect(s).toEqual({ text: "Nothing abnormal", tone: "good" });
  });
});


describe("in their words", () => {
  const level = (text: string) => find(call({ survey_responses: { overall_feeling: text } }), "overall")?.level;
  it("does not paint a bad answer green", () => {
    expect(level("very poor")).toBe("watch");
    expect(level("poor")).toBe("watch");
    expect(level("not good")).toBe("watch");
    expect(level("terrible")).toBe("alert");
  });
  it("keeps good answers normal and unknown words neutral", () => {
    expect(level("great")).toBe("normal");
    expect(level("fair")).toBe("unknown");
  });
});


describe("19 Oct 2026 questionnaire", () => {
  const q = (over: Record<string, unknown>) => call({ survey_responses: over });

  it("old calls show the new questions as not asked", () => {
    const c = q({ pain_present: "no", fever_reported: "no", incision_concerns: "no" });
    expect(find(c, "legs")?.level).toBe("unasked");
    expect(find(c, "sleep")?.level).toBe("unasked");
    expect(find(c, "questions")?.value).toBe("Not asked");
    expect(find(c, "legs")?.predates).toBe(true);
  });

  it("a new-questionnaire call that missed the new questions counts them as unanswered", () => {
    const c = call({
      initiated_at: "2026-10-20T10:00:00Z",
      survey_responses: { pain_present: "no", fever_reported: "no", incision_concerns: "no" },
    });
    expect(find(c, "legs")?.predates).toBeUndefined();
    expect(summarize(findingsFor(c))?.text).toBe("3 not answered");
  });

  it("a new call that answered everything normally clears", () => {
    const c = call({
      initiated_at: "2026-10-20T10:00:00Z",
      survey_responses: {
        pain_present: "no", fever_reported: "no", incision_concerns: "no",
        legs_swollen: "no", sleeping_well: "yes", has_questions: "no",
      },
    });
    expect(summarize(findingsFor(c))).toEqual({ text: "Nothing abnormal", tone: "good" });
  });

  it("pain location is shown as the patient's own description", () => {
    const f = find(q({ pain_present: "yes", pain_severity: 5, pain_location: "a dull ache around the knee" }), "pain");
    expect(f?.detail).toBe("a dull ache around the knee");
  });

  describe("leg swelling", () => {
    it("no is normal", () => {
      expect(find(q({ legs_swollen: "no" }), "legs")?.level).toBe("normal");
    });
    it("both legs, stable, is a watch", () => {
      const f = find(q({ legs_swollen: "yes", swelling_description: "both ankles a bit puffy, about the same" }), "legs");
      expect(f?.level).toBe("watch");
      expect(f?.detail).toContain("both ankles");
    });
    it("yes with no description is a watch", () => {
      expect(find(q({ legs_swollen: "yes" }), "legs")?.level).toBe("watch");
    });
    it.each([
      "just the left leg",
      "only my right calf",
      "one leg, and it is getting worse",
      "both, and they are painful",
      "the calf is red and warm",
    ])("alerts on '%s'", (desc) => {
      expect(find(q({ legs_swollen: "yes", swelling_description: desc }), "legs")?.level).toBe("alert");
    });
    it("negated words do not alert", () => {
      expect(find(q({ legs_swollen: "yes", swelling_description: "both legs, not getting worse, no pain" }), "legs")?.level)
        .toBe("watch");
    });
    it("unknown is unsure", () => {
      expect(find(q({ legs_swollen: "unknown" }), "legs")?.value).toBe("Unsure");
    });
  });

  describe("sleep", () => {
    it("not sleeping well is a watch", () => {
      expect(find(q({ sleeping_well: "no" }), "sleep")).toMatchObject({ value: "Not sleeping well", level: "watch" });
    });
    it("sleeping well is normal", () => {
      expect(find(q({ sleeping_well: "yes" }), "sleep")?.level).toBe("normal");
    });
  });

  describe("blood sugar reading", () => {
    it("shows the patient's words as detail", () => {
      const f = find(q({ blood_sugar_status: "high", blood_sugar_reading: "between 120 and 250" }), "sugar");
      expect(f?.level).toBe("alert");
      expect(f?.detail).toBe("between 120 and 250");
    });
    it("a reading without a status still shows", () => {
      const f = find(q({ blood_sugar_reading: "around 140" }), "sugar");
      expect(f?.detail).toBe("around 140");
      expect(f?.level).toBe("unknown");
    });
  });

  describe("questions for the care team", () => {
    it("has_questions no is a normal None", () => {
      expect(find(q({ has_questions: "no" }), "questions")).toMatchObject({ value: "None", level: "normal" });
    });
    it("an immediate question is an alert", () => {
      const f = find(q({
        has_questions: "yes",
        patient_questions: [
          { text: "When is my follow-up?", category: "appointment", priority: "routine_next_business_day" },
          { text: "Can I take ibuprofen?", category: "medication", priority: "urgent_same_day" },
        ],
      }), "questions");
      expect(f?.level).toBe("alert");
      expect(f?.value).toBe("2 questions · 1 immediate");
      expect(f?.detail).toBe("Medication, Appointment");
    });
    it("routine questions only are a watch", () => {
      const f = find(q({
        has_questions: "yes",
        patient_questions: [{ text: "When can I drive?", category: "activity", priority: "routine_next_business_day" }],
      }), "questions");
      expect(f).toMatchObject({ value: "1 question", level: "watch" });
    });
    it("yes with none captured is a watch telling the nurse to ask", () => {
      expect(find(q({ has_questions: "yes", patient_questions: [] }), "questions"))
        .toMatchObject({ value: "Yes — none captured", level: "watch" });
    });
    it("comes last, after the clinical answers", () => {
      const fs = findingsFor(call({
        dob_verification_result: "verified",
        survey_responses: { pain_present: "no", overall_feeling: "fine", has_questions: "no", legs_swollen: "no" },
      }));
      expect(fs[fs.length - 1].key).toBe("questions");
      expect(fs.findIndex((f) => f.key === "legs")).toBeLessThan(fs.findIndex((f) => f.key === "dob"));
    });
  });
});
