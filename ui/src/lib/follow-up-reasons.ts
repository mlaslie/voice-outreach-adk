import type { CallDoc, FollowUpEvent, FollowUpOverrideRequest, FollowUpReason } from "./types";

/**
 * Follow-up reasons: every finding on a call with its own tier.
 *
 * Calls since Oct 2026 carry `follow_up_reasons` (triage lists each finding,
 * most urgent first). Older calls have only the `follow_up_reason` sentence,
 * which is shown as a single reason at the call's tier. After a nurse change
 * the list is the nurse's, and the agent's originals are kept alongside.
 */

/** Most urgent first. Unknown tiers sort after "none". */
export const REASON_TIER_ORDER = [
  "emergency",
  "urgent_same_day",
  "routine_next_business_day",
  "informational",
  "none",
] as const;

/** Short labels for a reason's tier pill. */
export const REASON_TIER_SHORT: Record<string, string> = {
  emergency: "Emergency",
  urgent_same_day: "Same day",
  routine_next_business_day: "Next day",
  informational: "Noted",
  none: "No follow-up",
};

export function reasonTierLabel(tier: string | undefined): string {
  if (!tier) return "—";
  return REASON_TIER_SHORT[tier] ?? tier;
}

/** The "Change follow-up" dialog's preset reasons. */
export const FOLLOW_UP_REASON_PRESETS: readonly string[] = [
  "Medication question",
  "Medication refill",
  "Pain",
  "Wound or incision concern",
  "Fever",
  "Leg or foot swelling",
  "Not sleeping well",
  "Blood sugar out of range",
  "Patient question (other)",
  "Patient asked for a callback",
  "Discussed with patient — resolved",
];

/** Backend limits on POST /calls/{id}/follow-up. */
export const MAX_REASONS = 10;
export const MAX_REASON_LENGTH = 200;
export const MIN_NOTE_LENGTH = 3;

function rank(tier: string | undefined): number {
  const i = (REASON_TIER_ORDER as readonly string[]).indexOf(tier ?? "");
  return i === -1 ? REASON_TIER_ORDER.length : i;
}

/** Most urgent first; equal tiers keep their order. */
export function sortReasons(reasons: readonly FollowUpReason[]): FollowUpReason[] {
  return reasons
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank(a.r.tier) - rank(b.r.tier) || a.i - b.i)
    .map(({ r }) => r);
}

function clean(list: readonly FollowUpReason[] | undefined | null): FollowUpReason[] {
  return (list ?? []).filter((r) => r && typeof r.text === "string" && r.text.trim());
}

/**
 * The call's current reasons, most urgent first. An older call without a
 * list gets one reason from its sentence at the call's tier.
 */
export function reasonsFor(call: CallDoc | null | undefined): FollowUpReason[] {
  if (!call) return [];
  const list = clean(call.follow_up_reasons);
  if (list.length) return sortReasons(list);
  const text = call.follow_up_reason?.trim();
  if (!text) return [];
  // An old-style nurse change left the agent's sentence in follow_up_reason,
  // so it belongs at the agent's tier.
  const tier = call.follow_up_override ? (call.agent_follow_up ?? call.follow_up) : call.follow_up;
  return [{ tier: tier ?? "none", text, source: "agent" }];
}

/** True when a nurse set the current reasons list. */
export function reasonsSetByNurse(call: CallDoc | null | undefined): boolean {
  return clean(call?.follow_up_reasons).some((r) => r.source === "nurse");
}

/** Who set the nurse's reasons, and when (from the reasons, else the override). */
export function nurseReasonsMeta(call: CallDoc | null | undefined): { by: string; at?: string } | null {
  if (!call || !reasonsSetByNurse(call)) return null;
  const r = clean(call.follow_up_reasons).find((x) => x.source === "nurse");
  return {
    by: r?.set_by ?? call.follow_up_override?.set_by ?? "nurse",
    at: r?.at ?? call.follow_up_override?.at,
  };
}

/**
 * The agent's original reasons after a nurse replaced them; empty when the
 * nurse has not. Falls back to the agent's kept sentence.
 */
export function agentOriginalReasons(call: CallDoc | null | undefined): FollowUpReason[] {
  if (!call || !reasonsSetByNurse(call)) return [];
  const list = clean(call.agent_follow_up_reasons);
  if (list.length) return sortReasons(list);
  const text = call.agent_follow_up_reason?.trim();
  if (!text) return [];
  return [{ tier: call.agent_follow_up ?? "none", text, source: "agent" }];
}

export interface ReasonSummary {
  top: FollowUpReason | null;
  rest: FollowUpReason[];
  /** e.g. "question asked (medication) +1 more" */
  label: string;
  /** The rest, one per line, for a tooltip. Empty when there is nothing more. */
  tooltip: string;
}

/** The top reason plus "+N more" for compact rows. */
export function summariseReasons(reasons: readonly FollowUpReason[]): ReasonSummary {
  const sorted = sortReasons(reasons);
  const [top = null, ...rest] = sorted;
  return {
    top,
    rest,
    label: top ? (rest.length ? `${top.text} +${rest.length} more` : top.text) : "",
    tooltip: rest.map((r) => `${reasonTierLabel(r.tier)}: ${r.text}`).join("\n"),
  };
}

/** Collapse whitespace, drop blanks and case-insensitive duplicates (first wins). */
export function normaliseReasonTexts(texts: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of texts) {
    const s = (t ?? "").split(/\s+/).filter(Boolean).join(" ");
    const k = s.toLowerCase();
    if (!s || seen.has(k)) continue;
    seen.add(k);
    out.push(s);
  }
  return out;
}

/** The dialog's starting selection: the call's current reason texts that fit the limits. */
export function initialReasonSelection(call: CallDoc | null | undefined): string[] {
  return normaliseReasonTexts(reasonsFor(call).map((r) => r.text))
    .filter((t) => t.length <= MAX_REASON_LENGTH)
    .slice(0, MAX_REASONS);
}

/** Why Save is disabled, or null when the selection can be sent. */
export function overrideProblem(reasons: readonly string[], note: string): string | null {
  const list = normaliseReasonTexts(reasons);
  if (!list.length) return "Choose at least one reason";
  if (list.length > MAX_REASONS) return `At most ${MAX_REASONS} reasons`;
  if (list.some((r) => r.length > MAX_REASON_LENGTH)) return `Each reason is at most ${MAX_REASON_LENGTH} characters`;
  const n = note.trim();
  if (n && n.length < MIN_NOTE_LENGTH) return "The note needs a few more characters";
  return null;
}

/** The request body for POST /calls/{id}/follow-up. */
export function buildOverrideBody(tier: string, reasons: readonly string[], note: string): FollowUpOverrideRequest {
  const body: FollowUpOverrideRequest = { follow_up: tier, reasons: normaliseReasonTexts(reasons) };
  const n = note.trim();
  if (n) body.reason = n;
  return body;
}

/** True when the selection differs from the call's current reasons (order and case ignored). */
export function reasonsChanged(call: CallDoc | null | undefined, selected: readonly string[]): boolean {
  const key = (xs: readonly string[]) => normaliseReasonTexts(xs).map((x) => x.toLowerCase()).sort().join("\u0000");
  return key(reasonsFor(call).map((r) => r.text)) !== key(selected);
}

/**
 * What the backend does to a call on a nurse change, for sample data that
 * never leaves the browser. Mirrors routes_calls.follow_up_override.
 */
export function applyOverrideLocally(call: CallDoc, body: FollowUpOverrideRequest, by: string, now: string): CallDoc {
  const reasons = body.reasons ? normaliseReasonTexts(body.reasons) : [];
  const event: FollowUpEvent = {
    at: now,
    from: call.follow_up ?? null,
    to: body.follow_up,
    reason: body.reason || reasons.join("; "),
    set_by: by,
  };
  const next: CallDoc = { ...call };
  if (next.agent_follow_up === undefined) next.agent_follow_up = call.follow_up ?? null;
  if (reasons.length) {
    event.reasons = reasons;
    if (next.agent_follow_up_reasons === undefined) {
      next.agent_follow_up_reasons = call.follow_up_reasons ?? [];
      next.agent_follow_up_reason = call.follow_up_reason ?? null;
    }
    next.follow_up_reasons = reasons.map((text) => ({ tier: body.follow_up, text, source: "nurse", set_by: by, at: now }));
    next.follow_up_reason = `Nurse: ${reasons.join("; ")}.`;
  }
  next.follow_up = body.follow_up;
  next.follow_up_override = event;
  next.follow_up_history = [...(call.follow_up_history ?? []), event];
  next.updated_at = now;
  return next;
}
