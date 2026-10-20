import { describe, expect, it, vi } from "vitest";
import { BatchDeleteResponse, CallDoc, CallListEntry } from "../types";
import { CompletedItem, completedItems, inScope, matchesCompleted } from "./model";
import {
  DELETE_CHUNK,
  chunk,
  confirmText,
  deleteInChunks,
  effectiveSelection,
  planDeletion,
  removedIds,
  selectAllState,
  summariseResults,
  toggleAll,
  toggleOne,
} from "./delete-calls";

const NOW = new Date(2026, 9, 7, 12, 0); // Wed 7 Oct 2026, noon local
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();
const sid = (n: number) => "CA" + n.toString(16).padStart(32, "0");

// Fictional calls only.
const call = (n: number, p: Partial<CallDoc> = {}): CallDoc => ({
  doc_id: sid(n),
  call_sid: sid(n),
  to: "+12025550101",
  from: "+12025550100",
  patient_name: `Test Patient ${n}`,
  initiated_at: hoursAgo(1),
  updated_at: hoursAgo(1),
  ended_at: hoursAgo(1),
  call_status: "success",
  ...p,
});

describe("selection under filters", () => {
  const calls = [
    call(1, { call_status: "success" }),
    call(2, { call_status: "rna" }),
    call(3, { call_status: "rna", ended_at: hoursAgo(24 * 3) }), // last week
    call(4, { call_status: "left_vmail" }),
  ];
  const items = completedItems(calls, []);
  const shown = (scope: "today" | "week" | "all", filter: "all" | "no_answer") =>
    items.filter((i) => inScope(i, scope, NOW) && matchesCompleted(i, filter));

  it("select all covers exactly the rows shown under the active range and filter", () => {
    const rows = shown("today", "no_answer");
    expect(rows.map((r) => r.id)).toEqual([sid(2)]);
    const sel = toggleAll(new Set(), rows);
    expect([...sel]).toEqual([sid(2)]);
    expect(selectAllState(sel, rows)).toBe(true);
    // Widening the range shows the older unanswered call, unselected.
    const wider = shown("week", "no_answer");
    expect(selectAllState(sel, wider)).toBe("indeterminate");
    expect(effectiveSelection(toggleAll(sel, wider), wider)).toEqual(wider.map((r) => r.id));
  });

  it("a ticked row hidden by a filter is not part of the selection that counts", () => {
    const sel = toggleAll(new Set(), shown("all", "all"));
    expect(sel.size).toBe(4);
    const rows = shown("today", "no_answer");
    expect(effectiveSelection(sel, rows)).toEqual([sid(2)]);
  });

  it("select all on a fully selected list clears only the shown rows", () => {
    const all = shown("all", "all");
    const rows = shown("today", "no_answer");
    const sel = toggleAll(toggleAll(new Set(), all), rows);
    expect(sel.has(sid(2))).toBe(false);
    expect(sel.size).toBe(3);
  });

  it("toggles one row and reports the checkbox state", () => {
    const rows = shown("all", "all");
    expect(selectAllState(new Set(), rows)).toBe(false);
    const one = toggleOne(new Set(), sid(1));
    expect(selectAllState(one, rows)).toBe("indeterminate");
    expect(toggleOne(one, sid(1)).size).toBe(0);
    expect(selectAllState(new Set(), [])).toBe(false);
  });
});

describe("planDeletion", () => {
  const entry: CallListEntry = {
    entry_id: "e1", phone: "+12025550102", patient_name: "Test Patient C", patient_id: "P-1",
    is_diabetic: false, list_status: "canceled", attempts: 0,
    created_at: hoursAgo(5), updated_at: hoursAgo(2),
    canceled: { at: hoursAgo(2), reason: "Patient asked", by: "nurse" },
  } as CallListEntry;

  it("skips calls in progress, judged on the freshest copy, and cancelled queue entries", () => {
    const items = completedItems([call(1), call(2), call(3)], [], [], [entry]);
    expect(items).toHaveLength(4);
    // Call 2 was redialled and is ringing again in the live stream.
    const latest = new Map([[sid(2), call(2, { call_status: "in-progress" })]]);
    const plan = planDeletion(items, (id) => latest.get(id));
    expect(plan.callIds.sort()).toEqual([sid(1), sid(3)]);
    expect(plan).toMatchObject({ inProgress: 1, notCalls: 1 });
    const text = confirmText(plan);
    expect(text.title).toBe("Delete 2 calls?");
    expect(text.body).toMatch(/permanently removes the call records, answers, transcripts and recordings/);
    expect(text.skipped).toEqual([
      "1 call in progress is skipped.",
      "1 cancelled queue entry is not a call and is kept.",
    ]);
  });

  it("treats a call with no status as in progress and says so in the plural", () => {
    const items: CompletedItem[] = completedItems([call(1)], []);
    const live = (id: string) => (id === sid(1) ? call(1, { call_status: undefined }) : undefined);
    const plan = planDeletion([...items, ...items], live);
    expect(plan.callIds).toEqual([]);
    expect(confirmText(plan)).toMatchObject({ title: "Nothing to delete", skipped: ["2 calls in progress are skipped."] });
  });

  it("does not send the same call twice", () => {
    const items = completedItems([call(1)], []);
    expect(planDeletion([...items, ...items]).callIds).toEqual([sid(1)]);
  });
});

describe("chunking", () => {
  it("splits into 100s", () => {
    const ids = Array.from({ length: 237 }, (_, i) => sid(i));
    expect(DELETE_CHUNK).toBe(100);
    expect(chunk(ids).map((c) => c.length)).toEqual([100, 100, 37]);
    expect(chunk([])).toEqual([]);
    expect(chunk(ids.slice(0, 100))).toHaveLength(1);
    expect(() => chunk(ids, 0)).toThrow();
  });

  it("sends each chunk once, reports progress, and keeps going after a failed request", async () => {
    const ids = Array.from({ length: 237 }, (_, i) => sid(i));
    let n = 0;
    const send = vi.fn(async (part: string[]): Promise<BatchDeleteResponse> => {
      if (n++ === 1) throw new Error("network");
      return { results: part.map((call_sid) => ({ call_sid, status: "deleted" as const })) };
    });
    const progress: number[] = [];
    const results = await deleteInChunks(ids, send, (_r, done) => progress.push(done));
    expect(send).toHaveBeenCalledTimes(3);
    expect(send.mock.calls.map(([p]) => p.length)).toEqual([100, 100, 37]);
    expect(progress).toEqual([100, 200, 237]);
    expect(results).toHaveLength(237);
    expect(summariseResults(results)).toMatchObject({ deleted: 137, failed: 100 });
  });

  it("counts an id the server did not answer for as not deleted", async () => {
    const results = await deleteInChunks([sid(1), sid(2)], async () => ({
      results: [{ call_sid: sid(1), status: "deleted" }],
    }));
    expect(results.map((r) => r.status)).toEqual(["deleted", "error"]);
  });
});

describe("summariseResults", () => {
  it("says how many were deleted and how many could not be", () => {
    const s = summariseResults([
      ...Array.from({ length: 37 }, (_, i) => ({ call_sid: sid(i), status: "deleted" as const })),
      { call_sid: sid(90), status: "error" },
      { call_sid: "bad", status: "invalid_sid" },
    ]);
    expect(s.message).toBe("Deleted 37 calls · 2 could not be deleted");
    expect(summariseResults([{ call_sid: sid(1), status: "deleted" }]).message).toBe("Deleted 1 call");
  });

  it("counts a call that was already gone as removed, not failed", () => {
    const results = [
      { call_sid: sid(1), status: "deleted" as const },
      { call_sid: sid(2), status: "not_found" as const },
      { call_sid: sid(3), status: "partial" as const },
    ];
    expect(summariseResults(results)).toMatchObject({
      deleted: 1, alreadyGone: 1, failed: 1,
      message: "Deleted 1 call · 1 was already gone · 1 could not be deleted",
    });
    expect(removedIds(results)).toEqual([sid(1), sid(2)]);
  });
});
