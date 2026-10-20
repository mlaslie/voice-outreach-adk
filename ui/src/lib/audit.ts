/**
 * Admin → Audit log: the backend records each configuration change as its
 * route template ("PUT /api/call-settings/display"). Admins read plain words
 * ("Changed time display"); an action not listed here shows as it came.
 */

const ACTIONS: Record<string, string> = {
  "POST /api/call-list": "Added a patient",
  "POST /api/call-list/bulk": "Imported patients",
  "PATCH /api/call-list/{entry_id}": "Edited a patient",
  "DELETE /api/call-list/{entry_id}": "Removed a patient",
  "POST /api/call-list/reset": "Put completed patients back on the list",
  "POST /api/call-runs": "Scheduled a call run",
  "PATCH /api/call-runs/{run_id}": "Changed a call run",
  "DELETE /api/call-runs/{run_id}": "Cancelled a call run",
  "PUT /api/call-settings/recall": "Changed calling hours and auto-recall",
  "PUT /api/call-settings/display": "Changed time display",
  "PUT /api/call-settings/organization": "Changed the company name",
  "POST /api/allowlist": "Authorized a number",
  "DELETE /api/allowlist/{phone}": "Removed an authorized number",
  "POST /api/sms/consent": "Recorded text consent",
  "POST /api/sms/settings": "Changed the missed-call text limit",
  "POST /api/sms/control": "Switched the shared SMS number",
  "DELETE /api/sms/callbacks/{callback_id}": "Cancelled a booked callback",
  "POST /api/sms/test/start": "Started a text test",
  "POST /api/sms/test/reply": "Replied in a text test",
  "POST /api/test-call": "Started a browser test call",
  "POST /api/calls/batch-delete": "Deleted calls",
};

/** "PUT /api/call-settings/display" -> "Changed time display"; unknown actions unchanged. */
export function describeAction(action: string): string {
  const key = action.trim().replace(/\s+/g, " ");
  return ACTIONS[key] ?? action;
}

const TARGET_LABELS: Record<string, string> = {
  phone: "Number",
  entry_id: "Patient record",
  run_id: "Run",
  callback_id: "Callback",
  call_id: "Call",
};

/** {phone: "+1•••4567"} -> "Number +1•••4567"; several joined with " · "; "" when none. */
export function describeTarget(target: Record<string, unknown> | null | undefined): string {
  if (!target) return "";
  return Object.entries(target)
    .filter(([, v]) => v !== null && v !== undefined && String(v) !== "")
    .map(([k, v]) => `${TARGET_LABELS[k] ?? k} ${String(v)}`)
    .join(" · ");
}
