import { BatchDeleteResponse, BatchDeleteResult, CallDoc } from "../types";
import { isActive } from "../status";
import { CompletedItem } from "./model";

/**
 * Multi-select delete on the Call status board (cleaning up demo calls).
 *
 * Pure rules, so the screen only wires them up:
 *   - the selection that counts is the selected rows that are SHOWN now
 *     (after the range, filter and search); a row hidden by a filter is
 *     never deleted, even if it was ticked before the filter changed;
 *   - a call still in progress is skipped, judged on the freshest copy of
 *     the call, not the row's snapshot;
 *   - a cancelled queue entry is not a call record and is skipped;
 *   - the backend takes at most 100 ids per request.
 */

export const DELETE_CHUNK = 100;

/** The selected ids among the rows shown, in row order. */
export function effectiveSelection(selected: ReadonlySet<string>, rows: readonly { id: string }[]): string[] {
  return rows.filter((r) => selected.has(r.id)).map((r) => r.id);
}

/** The Select-all checkbox: checked, unchecked, or some of the shown rows. */
export function selectAllState(
  selected: ReadonlySet<string>,
  rows: readonly { id: string }[],
): boolean | "indeterminate" {
  const n = effectiveSelection(selected, rows).length;
  if (n === 0) return false;
  return n === rows.length ? true : "indeterminate";
}

/** Select every shown row, or, when all are already selected, clear them. */
export function toggleAll(selected: ReadonlySet<string>, rows: readonly { id: string }[]): Set<string> {
  const next = new Set(selected);
  if (selectAllState(selected, rows) === true) rows.forEach((r) => next.delete(r.id));
  else rows.forEach((r) => next.add(r.id));
  return next;
}

export function toggleOne(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export interface DeletePlan {
  /** Call doc ids to send, in row order. */
  callIds: string[];
  /** Selected calls still in progress: skipped. */
  inProgress: number;
  /** Selected cancelled queue entries: not call records, skipped. */
  notCalls: number;
}

/**
 * What a delete of these rows would actually remove. `latest` gives the
 * freshest copy of a call (the live stream may have moved on since the row
 * was drawn); without one the row's own copy is used.
 */
export function planDeletion(
  items: readonly CompletedItem[],
  latest?: (docId: string) => CallDoc | undefined,
): DeletePlan {
  const plan: DeletePlan = { callIds: [], inProgress: 0, notCalls: 0 };
  const seen = new Set<string>();
  for (const i of items) {
    if (!i.call) {
      plan.notCalls++;
      continue;
    }
    const c = latest?.(i.call.doc_id) ?? i.call;
    if (isActive(c)) {
      plan.inProgress++;
      continue;
    }
    if (seen.has(c.doc_id)) continue;
    seen.add(c.doc_id);
    plan.callIds.push(c.doc_id);
  }
  return plan;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The confirm dialog's words for a plan. */
export function confirmText(plan: DeletePlan): { title: string; body: string; skipped: string[] } {
  const n = plan.callIds.length;
  const skipped: string[] = [];
  if (plan.inProgress) {
    skipped.push(`${plural(plan.inProgress, "call", "calls")} in progress ${plan.inProgress === 1 ? "is" : "are"} skipped.`);
  }
  if (plan.notCalls) {
    skipped.push(
      `${plural(plan.notCalls, "cancelled queue entry", "cancelled queue entries")} ${plan.notCalls === 1 ? "is not a call and is" : "are not calls and are"} kept.`,
    );
  }
  return {
    title: n ? `Delete ${plural(n, "call", "calls")}?` : "Nothing to delete",
    body: n
      ? "This permanently removes the call records, answers, transcripts and recordings. It cannot be undone."
      : "None of the selected rows can be deleted.",
    skipped,
  };
}

export function chunk<T>(xs: readonly T[], size: number = DELETE_CHUNK): T[][] {
  if (size < 1) throw new Error("chunk size must be at least 1");
  const out: T[][] = [];
  for (let i = 0; i < xs.length; i += size) out.push(xs.slice(i, i + size));
  return out;
}

/**
 * Send ids in chunks of 100, one request at a time. A chunk whose request
 * fails counts every id in it as an error and the rest still go.
 * `onChunk` gets each chunk's results as they land (rows vanish as they go).
 */
export async function deleteInChunks(
  ids: readonly string[],
  send: (chunk: string[]) => Promise<BatchDeleteResponse>,
  onChunk?: (results: BatchDeleteResult[], done: number, total: number) => void,
): Promise<BatchDeleteResult[]> {
  const all: BatchDeleteResult[] = [];
  let done = 0;
  for (const part of chunk(ids)) {
    let results: BatchDeleteResult[];
    try {
      results = (await send(part)).results;
    } catch {
      results = part.map((call_sid) => ({ call_sid, status: "error" as const }));
    }
    // An id the server did not answer for was not deleted.
    const answered = new Set(results.map((r) => r.call_sid));
    for (const id of part) if (!answered.has(id)) results.push({ call_sid: id, status: "error" });
    all.push(...results);
    done += part.length;
    onChunk?.(results, done, ids.length);
  }
  return all;
}

/** Ids gone from the backend: deleted now, or already gone. */
export function removedIds(results: readonly BatchDeleteResult[]): string[] {
  return results.filter((r) => r.status === "deleted" || r.status === "not_found").map((r) => r.call_sid);
}

export interface DeleteSummary {
  deleted: number;
  alreadyGone: number;
  failed: number;
  message: string;
}

export function summariseResults(results: readonly BatchDeleteResult[]): DeleteSummary {
  const deleted = results.filter((r) => r.status === "deleted").length;
  const alreadyGone = results.filter((r) => r.status === "not_found").length;
  const failed = results.length - deleted - alreadyGone;
  const parts = [`Deleted ${plural(deleted, "call", "calls")}`];
  if (alreadyGone) parts.push(`${alreadyGone} ${alreadyGone === 1 ? "was" : "were"} already gone`);
  if (failed) parts.push(`${failed} could not be deleted`);
  return { deleted, alreadyGone, failed, message: parts.join(" · ") };
}
