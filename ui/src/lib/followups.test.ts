import { describe, expect, it } from "vitest";
import {
  FollowUpSession,
  ScheduledCallback,
  callbackLabel,
  isCancellable,
  isOpen,
  isOverdue,
  openConversations,
  sessionLabel,
  sortSessions,
  summarize,
  upcoming,
} from "./followups";
import type { SessionState } from "./followups";

const NOW = new Date("2026-08-13T15:00:00Z");

function cb(over: Partial<ScheduledCallback> = {}): ScheduledCallback {
  return {
    callback_id: "cb1",
    phone: "+12025550123",
    run_at: "2026-08-13T18:00:00Z",
    status: "scheduled",
    ...over,
  };
}

function session(over: Partial<FollowUpSession> = {}): FollowUpSession {
  return { phone: "+12025550123", state: "awaiting_choice", updated_at: "2026-08-13T14:00:00Z", ...over };
}

describe("labels", () => {
  it("never shows the state machine's own vocabulary", () => {
    // A nurse should not have to know what "recall_dispatched" means.
    expect(sessionLabel("recall_dispatched").text).toBe("Call queued");
    expect(sessionLabel("awaiting_choice").text).toBe("Waiting for reply");
    expect(sessionLabel("expired").text).toBe("No reply");
  });

  it("flags an opt-out loudly — it changes who may be contacted", () => {
    expect(sessionLabel("opted_out").tone).toBe("alert");
  });

  it("survives a state the backend adds later", () => {
    expect(sessionLabel("something_new" as never).text).toBe("something_new");
    expect(sessionLabel(undefined).text).toBe("Unknown");
  });

  it("a failed callback is an alert, a cancelled one is not", () => {
    expect(callbackLabel("failed").tone).toBe("alert");
    expect(callbackLabel("canceled").tone).toBe("muted");
  });
});

describe("isOpen", () => {
  it("counts every state where something is still going to happen", () => {
    for (const s of ["awaiting_choice", "awaiting_time", "scheduled", "recall_dispatched", "dialing"] as const) {
      expect(isOpen(s)).toBe(true);
    }
    for (const s of ["closed", "expired", "opted_out"] as const) {
      expect(isOpen(s)).toBe(false);
    }
  });
});

describe("upcoming", () => {
  it("shows only bookings that will still happen, soonest first", () => {
    const list = upcoming([
      cb({ callback_id: "late", run_at: "2026-08-14T10:00:00Z" }),
      cb({ callback_id: "done", status: "dispatched" }),
      cb({ callback_id: "soon", run_at: "2026-08-13T16:00:00Z" }),
      cb({ callback_id: "gone", status: "canceled" }),
    ]);
    expect(list.map((c) => c.callback_id)).toEqual(["soon", "late"]);
  });

  it("keeps a booking whose time has passed", () => {
    // Hiding it would hide the outage: still "scheduled" past its time
    // means the ticker never ran.
    const list = upcoming([cb({ run_at: "2026-08-13T09:00:00Z" })]);
    expect(list).toHaveLength(1);
  });
});

describe("isOverdue", () => {
  it("is true once a booking is more than five minutes late", () => {
    expect(isOverdue(cb({ run_at: "2026-08-13T14:50:00Z" }), NOW)).toBe(true);
  });

  it("allows a grace period so a booking mid-dial isn't flagged", () => {
    expect(isOverdue(cb({ run_at: "2026-08-13T14:58:00Z" }), NOW)).toBe(false);
  });

  it("is never true for a booking already acted on", () => {
    expect(isOverdue(cb({ run_at: "2026-08-13T09:00:00Z", status: "dispatched" }), NOW)).toBe(false);
    expect(isOverdue(cb({ run_at: "2026-08-13T09:00:00Z", status: "dialing" }), NOW)).toBe(false);
  });
});

describe("isCancellable", () => {
  it("only a booking that has not been acted on", () => {
    expect(isCancellable(cb({ status: "scheduled" }))).toBe(true);
    // Offering Cancel on a call already ringing would be a lie.
    expect(isCancellable(cb({ status: "dialing" }))).toBe(false);
    expect(isCancellable(cb({ status: "dispatched" }))).toBe(false);
  });
});

describe("summarize", () => {
  it("counts what a nurse has to act on", () => {
    const s = summarize(
      [
        session({ phone: "+1", state: "awaiting_choice" }),
        session({ phone: "+2", state: "awaiting_time" }),
        session({ phone: "+3", state: "scheduled" }),
        session({ phone: "+4", state: "opted_out" }),
        session({ phone: "+5", state: "closed" }),
      ],
      [cb({ callback_id: "a" }), cb({ callback_id: "b", run_at: "2026-08-13T09:00:00Z" })],
      NOW
    );
    expect(s).toEqual({ waiting: 2, booked: 2, optedOut: 1, overdue: 1 });
  });

  it("reports zeros rather than blanks when nothing is happening", () => {
    expect(summarize([], [], NOW)).toEqual({ waiting: 0, booked: 0, optedOut: 0, overdue: 0 });
  });
});

describe("sortSessions", () => {
  it("puts live conversations above finished ones", () => {
    const list = sortSessions([
      session({ phone: "+old", state: "closed", updated_at: "2026-08-13T14:59:00Z" }),
      session({ phone: "+live", state: "awaiting_choice", updated_at: "2026-08-13T10:00:00Z" }),
    ]);
    // Recency alone would have buried the one still needing an answer.
    expect(list[0].phone).toBe("+live");
  });

  it("orders by recency within the same group", () => {
    const list = sortSessions([
      session({ phone: "+a", state: "closed", updated_at: "2026-08-13T10:00:00Z" }),
      session({ phone: "+b", state: "closed", updated_at: "2026-08-13T14:00:00Z" }),
    ]);
    expect(list.map((s) => s.phone)).toEqual(["+b", "+a"]);
  });

  it("does not mutate its input", () => {
    const input = [session({ phone: "+a", state: "closed" }), session({ phone: "+b" })];
    sortSessions(input);
    expect(input[0].phone).toBe("+a");
  });
});

describe("openConversations — a worklist, with nothing listed twice", () => {
  const s = (state: SessionState, phone = "+1555"): FollowUpSession => ({
    phone,
    state,
  });

  it("drops a completed questionnaire", () => {
    // A patient who finished the survey by text stayed on the list as though
    // we were still waiting on them.
    const rows = [s("closed", "+1a"), s("awaiting_choice", "+1b")];
    expect(openConversations(rows).map((r) => r.phone)).toEqual(["+1b"]);
  });

  it("drops a booked callback, which is already listed above", () => {
    // It appeared under BOTH "Scheduled callbacks" and the conversation
    // list, and the conversation list mislabelled it too: the patient has
    // replied, so nothing is waiting on them.
    expect(openConversations([s("scheduled")])).toEqual([]);
  });

  it("keeps conversations that appear nowhere else", () => {
    const live: SessionState[] = [
      "awaiting_choice",
      "awaiting_time",
      "recall_dispatched",
      "dialing",
      "awaiting_dob",
      "survey",
    ];
    expect(openConversations(live.map((st) => s(st))).length).toBe(live.length);
  });

  it("drops expired and opted-out too", () => {
    expect(openConversations([s("expired"), s("opted_out")])).toEqual([]);
  });

  it("shows everything when asked", () => {
    const rows = [s("closed"), s("scheduled")];
    expect(openConversations(rows, true).length).toBe(2);
  });
});

describe("mid-survey states are recognised", () => {
  it("labels them rather than showing a raw enum", () => {
    // Both states were added to the backend before the dashboard knew them,
    // so a patient answering questions rendered as the literal string
    // "survey" and sorted as though the conversation were finished.
    expect(sessionLabel("awaiting_dob").text).toBe("Verifying identity");
    expect(sessionLabel("survey").text).toBe("Answering by text");
    expect(isOpen("survey")).toBe(true);
    expect(isOpen("awaiting_dob")).toBe(true);
  });
});
