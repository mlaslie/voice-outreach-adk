import { describe, expect, it } from "vitest";
import { followUpCalls, isSignedOff, needsFollowUp, summarizeFollowUps } from "./status";
import { CallDoc } from "./types";

function call(over: Partial<CallDoc>): CallDoc {
  return {
    doc_id: "CA1",
    to: "+12025550123",
    from: "+12025550111",
    initiated_at: "2026-07-29T10:00:00Z",
    updated_at: "2026-07-29T10:00:00Z",
    ...over,
  };
}

describe("needsFollowUp", () => {
  it.each([
    ["emergency", true],
    ["urgent_same_day", true],
    ["routine_next_business_day", true],
    ["informational", false],
    ["none", false],
    [undefined, false],
  ])("%s -> %s", (tier, expected) => {
    expect(needsFollowUp(call({ follow_up: tier as string | undefined }))).toBe(expected);
  });
});

describe("an unanswered redial does not clear a flag", () => {
  const base = { to: "+12025550101", from: "+12025550100", updated_at: "2026-10-16T12:00:00Z", patient_id: "P-1" };
  it("keeps the urgent text survey when a later call went to voicemail", () => {
    const calls = [
      { ...base, doc_id: "CAsurvey", initiated_at: "2026-10-16T11:50:00Z", call_status: "success", channel: "sms" as const,
        outcome: "questionnaire_completed", follow_up: "urgent_same_day" },
      { ...base, doc_id: "CAvm", initiated_at: "2026-10-16T13:50:00Z", call_status: "left_vmail", outcome: "voicemail_left" },
    ];
    expect(followUpCalls(calls).map((c) => c.doc_id)).toEqual(["CAsurvey"]);
    expect(summarizeFollowUps(calls).total).toBe(1);
  });
  it("ignores the follow_up='none' the voice agent files on a voicemail", () => {
    const calls = [
      { ...base, doc_id: "CAsurvey", initiated_at: "2026-10-16T11:50:00Z", outcome: "questionnaire_completed", follow_up: "urgent_same_day" },
      { ...base, doc_id: "CAvm", initiated_at: "2026-10-16T17:08:00Z", call_status: "left_vmail", outcome: "voicemail_left",
        follow_up: "none", follow_up_reason: "Voicemail left; no patient contact." },
    ];
    expect(followUpCalls(calls).map((c) => c.doc_id)).toEqual(["CAsurvey"]);
  });
  it("keeps it open after a later CLEAN questionnaire until a nurse resolves it (D-1)", () => {
    const calls = [
      { ...base, doc_id: "CAold", initiated_at: "2026-10-16T11:50:00Z", outcome: "questionnaire_completed", follow_up: "urgent_same_day" },
      { ...base, doc_id: "CAnew", initiated_at: "2026-10-16T13:50:00Z", outcome: "questionnaire_completed", follow_up: "none" },
    ];
    expect(followUpCalls(calls).map((c) => c.doc_id)).toEqual(["CAold"]);
    const resolved = [{ ...calls[0], review: { status: "superseded" as const, resolved: true } }, calls[1]];
    expect(followUpCalls(resolved)).toHaveLength(0);
  });
  it("lets a nurse's tier change on THAT report close it", () => {
    const calls = [
      { ...base, doc_id: "CAold", initiated_at: "2026-10-16T11:50:00Z", outcome: "questionnaire_completed", follow_up: "none",
        follow_up_override: { at: "2026-10-16T14:00:00Z", to: "none", reason: "Spoke with patient by phone." } },
    ];
    expect(followUpCalls(calls)).toHaveLength(0);
  });
});

describe("summarizeFollowUps", () => {
  it("counts each tier and picks the worst", () => {
    const s = summarizeFollowUps([
      call({ doc_id: "a", patient_id: "P1", follow_up: "routine_next_business_day" }),
      call({ doc_id: "b", patient_id: "P2", follow_up: "emergency" }),
      call({ doc_id: "c", patient_id: "P3", follow_up: "urgent_same_day" }),
      call({ doc_id: "d", patient_id: "P4", follow_up: "informational" }),
      call({ doc_id: "e", patient_id: "P5", follow_up: "none" }),
      call({ doc_id: "f", patient_id: "P6" }),
    ]);
    expect(s.total).toBe(3);
    expect(s.worst).toBe("emergency");
    expect(s.byTier.emergency).toBe(1);
    expect(s.byTier.routine_next_business_day).toBe(1);
  });

  it("counts patients, not call records, when a patient was redialled", () => {
    const s = summarizeFollowUps([
      call({ doc_id: "a", patient_id: "P1", follow_up: "urgent_same_day",
             initiated_at: "2026-07-29T10:00:00Z" }),
      call({ doc_id: "b", patient_id: "P1", follow_up: "urgent_same_day",
             initiated_at: "2026-07-29T11:00:00Z" }),
    ]);
    expect(s.total).toBe(1);
  });

  it("does not let a clean redial close an unresolved emergency (D-1)", () => {
    const s = summarizeFollowUps([
      call({ doc_id: "a", patient_id: "P1", follow_up: "emergency",
             initiated_at: "2026-07-29T10:00:00Z" }),
      call({ doc_id: "b", patient_id: "P1", follow_up: "none", outcome: "questionnaire_completed",
             initiated_at: "2026-07-29T12:00:00Z" }),
    ]);
    expect(s.total).toBe(1);
    expect(s.worst).toBe("emergency");
  });

  it("does not let an older clean call mask a newer urgent one", () => {
    const s = summarizeFollowUps([
      call({ doc_id: "a", patient_id: "P1", follow_up: "none",
             initiated_at: "2026-07-29T10:00:00Z" }),
      call({ doc_id: "b", patient_id: "P1", follow_up: "emergency",
             initiated_at: "2026-07-29T12:00:00Z" }),
    ]);
    expect(s.total).toBe(1);
    expect(s.worst).toBe("emergency");
  });

  it("groups calls without a patient_id by phone, never with an MRN (B-6)", () => {
    const s = summarizeFollowUps([
      call({ doc_id: "a", follow_up: "urgent_same_day" }),
      call({ doc_id: "b", follow_up: "urgent_same_day" }),
      call({ doc_id: "c", follow_up: "urgent_same_day", to: "+12025550109" }),
      call({ doc_id: "d", follow_up: "urgent_same_day", patient_id: "P9" }),
    ]);
    expect(s.total).toBe(3);
  });

  it("reports zero for an empty list", () => {
    const s = summarizeFollowUps([]);
    expect(s.total).toBe(0);
    expect(s.worst).toBeNull();
  });
});

describe("followUpCalls", () => {
  it("returns the same set the banner counts, so the filter matches", () => {
    const calls = [
      call({ doc_id: "a", patient_id: "P1", follow_up: "emergency",
             initiated_at: "2026-07-29T10:00:00Z" }),
      call({ doc_id: "b", patient_id: "P1", follow_up: "none", outcome: "questionnaire_completed",
             initiated_at: "2026-07-29T12:00:00Z" }),
      call({ doc_id: "c", patient_id: "P2", follow_up: "urgent_same_day" }),
    ];
    const shown = followUpCalls(calls);
    expect(shown.map((c) => c.doc_id).sort()).toEqual(["a", "c"]);   // D-1: a stays open
    expect(shown).toHaveLength(summarizeFollowUps(calls).total);
  });
});


describe("resolved follow-ups", () => {
  it("leave the follow-up count once signed off", () => {
    const base = { to: "+12025550101", from: "+12025550100", updated_at: "2026-10-17T12:00:00Z", patient_id: "P-1",
      initiated_at: "2026-10-17T11:00:00Z", outcome: "questionnaire_completed", follow_up: "urgent_same_day" };
    expect(followUpCalls([{ ...base, doc_id: "a" }])).toHaveLength(1);
    expect(followUpCalls([{ ...base, doc_id: "a", review: { status: "signed_off", resolved: true } }])).toHaveLength(0);
    // A reopened sign-off is outstanding again.
    expect(followUpCalls([{ ...base, doc_id: "a", review: { status: "reopened" } }])).toHaveLength(1);
  });
});

describe("a superseded report is closed", () => {
  it("counts as signed off, so it never shows as an open follow-up", () => {
    const c = call({ doc_id: "CAold", patient_id: "P-9", outcome: "questionnaire_completed", follow_up: "urgent_same_day",
                     review: { status: "superseded", by: "rn@example.org", superseded_by: "CAnew" } });
    expect(isSignedOff(c)).toBe(true);
    expect(followUpCalls([c])).toEqual([]);
  });
});
