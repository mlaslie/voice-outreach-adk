import { describe, expect, it } from "vitest";
import { CallDoc, CallRun } from "../types";
import { ScheduledCallback } from "../followups";
import {
  PlannedEntry,
  completedItems,
  dayHeading,
  dueFor,
  followUpItems,
  inScope,
  kindOf,
  matchesCompleted,
  matchesFollowUp,
  runOnQueue,
  scheduleItems,
} from "./model";
import { demoData } from "./demo";

const NOW = new Date(2026, 9, 7, 12, 0); // Wed 7 Oct 2026, noon local

const call = (p: Partial<CallDoc>): CallDoc => ({
  doc_id: p.call_sid ?? "CA1",
  to: "+12025550101",
  from: "+12025550100",
  initiated_at: "2026-10-07T10:00:00Z",
  updated_at: "2026-10-07T10:05:00Z",
  ...p,
});

const entry = (p: Partial<PlannedEntry>): PlannedEntry => ({
  entry_id: "e1",
  phone: "+12025550102",
  patient_name: "Jane Doe",
  patient_id: "P-1",
  patient_dob: "1960-01-01",
  is_diabetic: false,
  list_status: "pending",
  attempts: 0,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  ...p,
});

describe("scheduleItems (call runs)", () => {
  const T = (h: number, m = 0) => new Date(2026, 9, 7, h, m).toISOString();
  const run = (p: Partial<CallRun>): CallRun => ({
    run_id: "r1", scheduled_for: T(15), entry_ids: ["e1"], status: "scheduled",
    created_at: T(8), updated_at: T(8), ...p,
  });

  it("puts every member of a scheduled run on the queue at the run's time", () => {
    const items = scheduleItems(
      [entry({ entry_id: "e1" }), entry({ entry_id: "e2", patient_name: "B", attempts: 2 })],
      [run({ entry_ids: ["e1", "e2"], label: "Afternoon" })], [], [], [], NOW,
    );
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.runId === "r1")).toBe(true);
    expect(new Set(items.map((i) => i.entryId))).toEqual(new Set(["e1", "e2"]));
    expect(items.every((i) => i.state === "upcoming" && i.when === T(15))).toBe(true);
    expect(items.find((i) => i.entryId === "e2")?.origin).toBe("retry");
  });

  it("keeps members a running run has not reached yet on the queue, cancellable (B-2)", () => {
    const r = run({ entry_ids: ["e1", "e2", "e3"], status: "dispatched", scheduled_for: T(11), dispatched_at: T(11) });
    const items = scheduleItems([
      entry({ entry_id: "e1", list_status: "calling", last_attempt_at: T(11, 1) }),
      entry({ entry_id: "e2", patient_name: "B" }),                                          // waiting its turn
      entry({ entry_id: "e3", patient_name: "C", last_attempt_at: T(11, 5), last_call_status: "rna" }), // tried
    ], [r], [], [], [], NOW);
    const by = Object.fromEntries(items.map((i) => [i.entryId, i.state]));
    expect(by).toEqual({ e1: "calling", e2: "queued" });   // e3 was dialled: it leaves the queue
  });

  it("shows nobody waiting once the run has finished", () => {
    const r = run({ entry_ids: ["e2"], status: "dispatched", scheduled_for: T(11), dispatched_at: T(11),
                    finished_at: T(11, 30) });
    expect(scheduleItems([entry({ entry_id: "e2" })], [r], [], [], [], NOW)).toHaveLength(0);
  });

  it("does not show an auto-recall for a patient who booked a time by text", () => {
    const recall = run({ entry_ids: ["e1"], auto_recall: true, scheduled_for: T(15) });
    const cb = { callback_id: "cb1", phone: "+12025550102", status: "scheduled", run_at: T(14) } as unknown as ScheduledCallback;
    const items = scheduleItems([entry({ entry_id: "e1" })], [recall], [cb], [], [], NOW);
    expect(items.map((i) => i.origin)).toEqual(["text_reschedule"]);
  });

  it("leaves patients who are not in any run off the queue", () => {
    expect(scheduleItems([entry({})], [], [], [], [], NOW)).toHaveLength(0);
  });

  it("skips run members who were removed from the list", () => {
    expect(scheduleItems([], [run({})], [], [], [], NOW)).toHaveLength(0);
  });

  it("flags a run the scheduler has not started a few minutes after its time", () => {
    const [late] = scheduleItems([entry({ entry_id: "e1" })], [run({ scheduled_for: T(11, 50) })], [], [], [], NOW);
    expect(late.state).toBe("overdue");
    const [grace] = scheduleItems([entry({ entry_id: "e1" })], [run({ scheduled_for: T(11, 59) })], [], [], [], NOW);
    expect(grace.state).toBe("upcoming");
  });

  it("keeps only patients still on the phone from a dialled run", () => {
    const items = scheduleItems(
      [
        entry({ entry_id: "a", list_status: "calling", attempts: 1 }),
        entry({ entry_id: "b", patient_name: "B", list_status: "completed", attempts: 1 }),
        entry({ entry_id: "c", patient_name: "C", list_status: "pending", attempts: 1, last_call_status: "left_vmail", last_attempt_at: T(11, 2) }),
        entry({ entry_id: "d", patient_name: "D", list_status: "blocked", attempts: 1 }),
      ],
      [run({ scheduled_for: T(11), status: "dispatched", entry_ids: ["a", "b", "c", "d"] })],
      [], [], [], NOW,
    );
    // Reached, missed and blocked all left the queue: the Call status board
    // has them, and a missed patient returns as a future Auto-recall run.
    expect(items.map((i) => [i.entryId, i.state])).toEqual([["a", "calling"]]);
    // The run's own attempt does not make a first call a "retry".
    expect(items[0].origin).toBe("first_call");
  });

  it("drops a scheduled run's member who was reached before the run", () => {
    const items = scheduleItems(
      [entry({ entry_id: "a", list_status: "completed" }), entry({ entry_id: "b", patient_name: "B" })],
      [run({ entry_ids: ["a", "b"] })], [], [], [], NOW,
    );
    expect(items.map((i) => i.entryId)).toEqual(["b"]);
  });

  it("keeps a dialled run on its own day only", () => {
    const yesterday = run({ scheduled_for: new Date(2026, 9, 6, 11).toISOString(), status: "dispatched" });
    expect(scheduleItems([entry({ entry_id: "e1" })], [yesterday], [], [], [], NOW)).toHaveLength(0);
    expect(runOnQueue(run({ scheduled_for: T(9), status: "dispatched" }), NOW)).toBe(true);
    expect(runOnQueue(run({ scheduled_for: new Date(2026, 9, 9, 9).toISOString() }), NOW)).toBe(true);
  });

  it("turns a booked text callback into a text reschedule with the patient's words", () => {
    const cb: ScheduledCallback = {
      callback_id: "cb", phone: "+12025550103", run_at: "2026-10-07T20:00:00Z",
      status: "scheduled", patient_name: "Sam", origin_call_sid: "CA9",
    };
    const [item] = scheduleItems([], [], [cb], [{ phone: cb.phone, call_sid: "CA9", scheduled_for_text: "after 4" }], [], NOW);
    expect(item.origin).toBe("text_reschedule");
    expect(item.whenText).toBe("after 4");
    expect(item.entryId).toBeUndefined();
  });

  it("ignores cancelled and superseded callbacks", () => {
    const base = { phone: "+1", run_at: "2026-10-07T20:00:00Z" };
    const items = scheduleItems([], [], [
      { ...base, callback_id: "x", status: "canceled" },
      { ...base, callback_id: "y", status: "superseded" },
    ], [], [], NOW);
    expect(items).toHaveLength(0);
  });

  it("does not put a voice reschedule request on the queue (it has no time)", () => {
    const items = scheduleItems([], [], [], [], [
      call({ call_sid: "CA5", patient_id: "P-5", call_status: "success", outcome: "reschedule_requested" }),
    ], NOW);
    expect(items).toHaveLength(0);
    expect(kindOf(call({ call_status: "success", outcome: "reschedule_requested" }))).toBe("reschedule");
  });
});

describe("dayHeading", () => {
  it("names the near days and flags the past", () => {
    expect(dayHeading(new Date(2026, 9, 7, 18).toISOString(), NOW)).toBe("Today");
    expect(dayHeading(new Date(2026, 9, 8, 9).toISOString(), NOW)).toBe("Tomorrow");
    expect(dayHeading(new Date(2026, 9, 6, 9).toISOString(), NOW)).toBe("Overdue");
  });
});

describe("completed", () => {
  it("separates phone and text completions", () => {
    expect(kindOf(call({ call_status: "success" }))).toBe("completed_phone");
    expect(kindOf(call({ call_status: "success", channel: "sms" }))).toBe("completed_text");
  });

  it("does not count a reached patient who asked to reschedule as completed", () => {
    expect(kindOf(call({ call_status: "success", outcome: "reschedule_requested" }))).toBe("reschedule");
  });

  it("classifies the unreachable outcomes", () => {
    expect(kindOf(call({ call_status: "left_vmail" }))).toBe("voicemail");
    expect(kindOf(call({ call_status: "rna" }))).toBe("no_answer");
    expect(kindOf(call({ call_status: "busy" }))).toBe("unreachable");
    expect(kindOf(call({ call_status: "success", outcome: "unable_to_reach_wrong_person" }))).toBe("wrong_person");
  });

  it("leaves calls still in progress off the completed list", () => {
    expect(completedItems([call({ call_status: undefined }), call({ call_status: "ringing" })], [])).toHaveLength(0);
  });

  it("lets a voicemail that was followed by a text match both filters", () => {
    const [item] = completedItems(
      [call({ call_sid: "CA7", call_status: "left_vmail" })],
      [{ phone: "+1", call_sid: "CA7", last_invite_at: "2026-10-07T10:10:00Z" }],
    );
    expect(matchesCompleted(item, "voicemail")).toBe(true);
    expect(matchesCompleted(item, "text_sent")).toBe(true);
    expect(matchesCompleted(item, "completed_phone")).toBe(false);
  });
});

describe("follow-ups", () => {
  it("makes same-day due by 5 pm and next-business-day skip the weekend", () => {
    const fri = new Date(2026, 9, 9, 10, 0); // Friday
    expect(dueFor("urgent_same_day", fri).getHours()).toBe(17);
    const nbd = dueFor("routine_next_business_day", fri);
    expect(nbd.getDay()).toBe(1); // Monday
    expect(nbd.getDate()).toBe(12);
  });

  it("does not give a same-day item raised after 5 pm a due time in the past", () => {
    const late = new Date(2026, 9, 7, 19, 0);
    expect(dueFor("urgent_same_day", late).getTime()).toBe(late.getTime());
  });

  it("orders emergency first and marks overdue items", () => {
    const items = followUpItems([
      call({ call_sid: "A", patient_id: "1", follow_up: "routine_next_business_day", ended_at: "2026-10-07T15:00:00Z" }),
      call({ call_sid: "B", patient_id: "2", follow_up: "emergency", ended_at: "2026-10-07T15:00:00Z" }),
      call({ call_sid: "C", patient_id: "3", follow_up: "urgent_same_day", ended_at: new Date(2026, 9, 5, 9).toISOString() }),
    ], [], NOW);
    expect(items.map((i) => i.tier)).toEqual(["emergency", "urgent_same_day", "routine_next_business_day"]);
    expect(items.find((i) => i.id === "C")?.overdue).toBe(true);
    expect(matchesFollowUp(items.find((i) => i.id === "C")!, "overdue", NOW)).toBe(true);
  });

  it("never calls an emergency overdue — it is due from the moment it is raised", () => {
    const [item] = followUpItems([
      call({ call_sid: "E", follow_up: "emergency", ended_at: new Date(2026, 9, 7, 9).toISOString() }),
    ], [], NOW);
    expect(item.overdue).toBe(false);
  });

  it("keeps an unresolved urgent report after a later clean one (D-1), one row per patient", () => {
    const items = followUpItems([
      call({ call_sid: "old", patient_id: "9", follow_up: "urgent_same_day", initiated_at: "2026-10-06T10:00:00Z" }),
      call({ call_sid: "new", patient_id: "9", follow_up: "none", outcome: "questionnaire_completed", initiated_at: "2026-10-07T10:00:00Z" }),
      call({ call_sid: "older", patient_id: "9", follow_up: "routine_next_business_day", initiated_at: "2026-10-05T10:00:00Z" }),
    ], [], NOW);
    expect(items.map((i) => i.id)).toEqual(["old"]);
  });
});

describe("demo dataset", () => {
  it("exercises every origin, every completed kind and every follow-up tier", () => {
    const d = demoData(NOW);
    const sched = scheduleItems(d.entries, d.runs, d.callbacks, d.sessions, d.calls, NOW);
    expect(new Set(sched.map((s) => s.origin))).toEqual(
      new Set(["first_call", "retry", "text_reschedule"]),
    );
    // The sample queue never shows anything overdue: every planned time is
    // relative to now, and the scheduler is assumed to dial on time.
    expect(sched.filter((s) => s.state === "overdue")).toHaveLength(0);
    const done = completedItems(d.calls, d.sessions);
    for (const f of ["completed_phone", "completed_text", "voicemail", "text_sent", "no_answer", "unreachable", "interrupted", "reschedule"] as const) {
      expect(done.some((i) => matchesCompleted(i, f)), f).toBe(true);
    }
    const fu = followUpItems(d.calls, d.callbacks, NOW);
    expect(new Set(fu.map((f) => f.tier))).toEqual(
      new Set(["emergency", "urgent_same_day", "routine_next_business_day"]),
    );
  });

  it("uses only fictional 202-555-01xx numbers", () => {
    const d = demoData(NOW);
    const phones = [...d.calls.map((c) => c.to), ...d.entries.map((e) => e.phone)];
    for (const p of phones) expect(p).toMatch(/^\+120255501\d{2}$/);
  });
});


describe("call-status board (nurse HUD)", () => {
  const done = (p: Partial<CallDoc>) =>
    completedItems([call({ call_status: "success", outcome: "questionnaire_completed", ended_at: "2026-10-07T10:05:00Z", ...p })], [])[0];

  it("groups emergency and same-day as immediate follow-up", () => {
    expect(matchesCompleted(done({ follow_up: "emergency" }), "needs_immediate")).toBe(true);
    expect(matchesCompleted(done({ follow_up: "urgent_same_day" }), "needs_immediate")).toBe(true);
    expect(matchesCompleted(done({ follow_up: "routine_next_business_day" }), "needs_immediate")).toBe(false);
    expect(matchesCompleted(done({ follow_up: "routine_next_business_day" }), "next_business_day")).toBe(true);
  });

  it("counts only an assessed questionnaire as no follow-up needed", () => {
    expect(matchesCompleted(done({ follow_up: "none" }), "no_follow_up")).toBe(true);
    expect(matchesCompleted(done({ follow_up: "informational", channel: "sms" }), "no_follow_up")).toBe(true);
    // A voicemail has no verdict; it is not "nothing to do".
    const vm = completedItems([call({ call_status: "left_vmail", outcome: "voicemail_left", ended_at: "2026-10-07T10:05:00Z" })], [])[0];
    expect(matchesCompleted(vm, "no_follow_up")).toBe(false);
  });

  it("reads the nurse's override as the current tier", () => {
    const c = done({ follow_up: "none", agent_follow_up: "emergency",
      follow_up_override: { at: "2026-10-07T11:00:00Z", from: "emergency", to: "none", reason: "Spoke with patient." } });
    expect(c.overridden).toBe(true);
    expect(matchesCompleted(c, "no_follow_up")).toBe(true);
    expect(matchesCompleted(c, "needs_immediate")).toBe(false);
  });

  it("attaches the pending callback booked off the call", () => {
    const cb: ScheduledCallback = { callback_id: "cb1", phone: "+12025550101", run_at: "2026-10-07T19:30:00Z",
      status: "scheduled", origin_call_sid: "CA1" };
    const [item] = completedItems(
      [call({ call_sid: "CA1", call_status: "left_vmail", ended_at: "2026-10-07T10:05:00Z" })], [], [cb]);
    expect(item.callback?.callback_id).toBe("cb1");
    expect(matchesCompleted(item, "callback_booked")).toBe(true);
    const [cancelled] = completedItems(
      [call({ call_sid: "CA1", call_status: "left_vmail", ended_at: "2026-10-07T10:05:00Z" })], [], [{ ...cb, status: "canceled" }]);
    expect(cancelled.callback).toBeUndefined();
  });

  it("scopes the board to today, the week, or everything", () => {
    const today = done({ ended_at: "2026-10-07T15:00:00Z" });
    const lastWeek = done({ ended_at: "2026-10-02T15:00:00Z" });
    const old = done({ ended_at: "2026-09-01T15:00:00Z" });
    expect(inScope(today, "today", NOW)).toBe(true);
    expect(inScope(lastWeek, "today", NOW)).toBe(false);
    expect(inScope(lastWeek, "week", NOW)).toBe(true);
    expect(inScope(old, "week", NOW)).toBe(false);
    expect(inScope(old, "all", NOW)).toBe(true);
  });
});


describe("follow-up counts stay in step", () => {
  const base = { to: "+12025550101", from: "+12025550100", updated_at: "2026-10-07T12:00:00Z", patient_id: "P-1" };
  const survey = { ...base, doc_id: "CAs", call_sid: "CAs", initiated_at: "2026-10-07T11:50:00Z", ended_at: "2026-10-07T11:57:00Z",
    call_status: "success", channel: "sms" as const, outcome: "questionnaire_completed", follow_up: "urgent_same_day" };
  const vm = { ...base, doc_id: "CAv", call_sid: "CAv", initiated_at: "2026-10-07T13:50:00Z", ended_at: "2026-10-07T13:51:00Z",
    call_status: "left_vmail", outcome: "voicemail_left" };

  it("an unanswered redial does not clear an urgent text survey", () => {
    const items = followUpItems([survey, vm], [], NOW);
    expect(items.map((i) => i.id)).toEqual(["CAs"]);
  });

  it("the board's immediate filter counts the same patients as the follow-up list", () => {
    const done = completedItems([survey, vm], []);
    const immediate = done.filter((i) => matchesCompleted(i, "needs_immediate"));
    expect(immediate.map((i) => i.id)).toEqual(followUpItems([survey, vm], [], NOW).map((i) => i.id));
    // D-1: a newer CLEAN assessment does not close it, in either place; and
    // that clean report is not counted as "no follow-up" while it is open.
    const cleared = { ...vm, doc_id: "CAc", call_sid: "CAc", call_status: "success", outcome: "questionnaire_completed", follow_up: "none" };
    expect(followUpItems([survey, cleared], [], NOW).map((i) => i.id)).toEqual(["CAs"]);
    const board = completedItems([survey, cleared], []);
    expect(board.filter((i) => matchesCompleted(i, "needs_immediate")).map((i) => i.id)).toEqual(["CAs"]);
    expect(board.filter((i) => matchesCompleted(i, "no_follow_up"))).toHaveLength(0);
  });
});


describe("cancelled queued calls", () => {
  it("appear on the board with the reason, until rescheduled", () => {
    const cancel = { at: "2026-10-07T11:00:00Z", reason: "Readmitted overnight.", by: "rn.lee" };
    const items = completedItems([], [], [], [
      entry({ entry_id: "e1", list_status: "canceled", canceled: cancel }),
      entry({ entry_id: "e2", patient_name: "B", list_status: "pending" }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe("canceled");
    expect(items[0].cancel?.reason).toBe("Readmitted overnight.");
    expect(items[0].call).toBeUndefined();
    expect(matchesCompleted(items[0], "canceled")).toBe(true);
    expect(matchesCompleted(items[0], "needs_immediate")).toBe(false);
    expect(inScope(items[0], "today", NOW)).toBe(true);
  });

  it("are not on the queue", () => {
    const run: CallRun = { run_id: "r", scheduled_for: new Date(2026, 9, 7, 15).toISOString(), entry_ids: ["e1"],
      status: "scheduled", created_at: "", updated_at: "" };
    expect(scheduleItems([entry({ entry_id: "e1", list_status: "canceled" })], [run], [], [], [], NOW)).toHaveLength(0);
  });
});

describe("languages on the queue (docs/LANGUAGES-PLAN.md)", () => {
  it("a held patient shows as held, cannot be mistaken for upcoming, and keeps its language", async () => {
    const { holdReason } = await import("../languages");
    const sample = (await import("../languages-sample.json")).default;
    const d = demoData(NOW);
    const items = scheduleItems(d.entries, d.runs, d.callbacks, d.sessions, d.calls, NOW, (e) => holdReason(e, sample));
    const lucia = items.find((i) => i.patientName === "Lucía Herrera")!;
    expect(lucia.state).toBe("held");
    expect(lucia.held).toBe("Waiting for Spanish (US) support");
    expect(lucia.language).toBe("es-US");
    const minh = items.find((i) => i.patientName === "Nguyen Van Minh")!;
    expect(minh.state).not.toBe("held"); // Call in English is on
    expect(minh.language).toBe("vi-VN");
  });

  it("without a hold check nothing is held", () => {
    const d = demoData(NOW);
    expect(scheduleItems(d.entries, d.runs, d.callbacks, d.sessions, d.calls, NOW).some((i) => i.state === "held")).toBe(false);
  });
});
