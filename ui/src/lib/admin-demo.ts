/**
 * Fictional sample data for the admin console's Messaging, Call diagnostics
 * and Audit log (/admin?data=demo). Every name, number, message and person
 * is invented. Nothing here is ever sent to the API.
 */

import { AuditEntry, CallDoc, CallEvent, CallEventsResponse } from "./types";
import { FollowUpSession, SmsMessage } from "./followups";
import { AdminReads } from "./nurse/actions";
import { NurseData } from "./nurse/demo";

const ORG = "the clinic";

/** The nurse sample's text sessions, plus a questionnaire in progress and an opt-out. */
export function demoAdminSessions(data: NurseData, now: Date = new Date()): FollowUpSession[] {
  const at = (h: number) => new Date(now.getTime() + h * 3_600_000).toISOString();
  const by = (name: string) => data.calls.find((c) => c.patient_name === name);
  const extra: FollowUpSession[] = [];
  const lowell = by("Beatrice Lowell");
  if (lowell) {
    extra.push({ phone: lowell.to, call_sid: lowell.call_sid, patient_name: lowell.patient_name, patient_id: lowell.patient_id,
      state: "survey", last_invite_at: at(-1.7), last_inbound_at: at(-0.3), updated_at: at(-0.3), message_count: 6 });
  }
  const kim = by("Walter Kim");
  if (kim) {
    extra.push({ phone: kim.to, call_sid: kim.call_sid, patient_name: kim.patient_name, patient_id: kim.patient_id,
      state: "opted_out", last_invite_at: at(-7.9), last_inbound_at: at(-7.5), updated_at: at(-7.5), message_count: 3 });
  }
  // Give every sample session a last-activity time.
  const sessions = data.sessions.map((s) => ({ ...s, updated_at: s.updated_at ?? s.last_inbound_at ?? s.last_invite_at ?? undefined }));
  return [...sessions, ...extra];
}

/** A fictional thread that fits the session's state. */
export function demoMessages(session: FollowUpSession): SmsMessage[] {
  const first = session.patient_name?.split(" ")[0] ?? "there";
  const start = new Date(session.last_invite_at ?? session.updated_at ?? Date.now()).getTime();
  let n = 0;
  const msg = (direction: SmsMessage["direction"], body: string, minutes: number): SmsMessage => ({
    id: `demo-${session.phone}-${++n}`,
    phone: session.phone,
    direction,
    body,
    at: new Date(start + minutes * 60_000).toISOString(),
    call_sid: session.call_sid ?? null,
  });
  const invite = msg("outbound",
    `Hi ${first}, this is ${ORG} following up after your procedure. We tried to call you. Reply 1 to answer a few questions by text, 2 to pick a time for a call back, or STOP to opt out.`, 0);
  switch (session.state) {
    case "awaiting_choice":
      return [invite];
    case "scheduled":
    case "recall_dispatched":
    case "dialing":
      return [
        invite,
        msg("inbound", "2", 18),
        msg("outbound", "When would you like us to call? For example: tomorrow at 10am.", 18.2),
        msg("inbound", session.scheduled_for_text ?? "tomorrow morning", 24),
        msg("outbound", "Thanks. We will call you then. Reply STOP to opt out.", 24.2),
      ];
    case "awaiting_dob":
    case "survey":
      return [
        invite,
        msg("inbound", "1", 9),
        msg("outbound", "To confirm it's you, please reply with your date of birth (MM/DD/YYYY).", 9.2),
        msg("inbound", "04/12/1958", 12),
        msg("outbound", "Thank you. On a scale of 0 to 10, how much pain are you in today?", 12.2),
        msg("inbound", "3", 80),
      ];
    case "opted_out":
      return [invite, msg("inbound", "STOP", 20), msg("outbound", "You are unsubscribed and will receive no more texts.", 20.1)];
    case "closed":
      return [
        invite,
        msg("inbound", "1", 6),
        msg("outbound", "To confirm it's you, please reply with your date of birth (MM/DD/YYYY).", 6.2),
        msg("inbound", "04/12/1958", 8),
        msg("outbound", "Thank you. That's all our questions. A nurse will review your answers.", 40),
      ];
    default:
      return [invite];
  }
}

/** Stable small number per call, so a sample timeline does not change between clicks. */
function seed(s: string): number {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

/** A fictional gateway timeline for a sample call (names and statuses only). */
export function demoCallEvents(call: CallDoc): CallEventsResponse {
  const id = call.call_sid ?? call.doc_id;
  const status = call.call_status ?? "";
  const none = call.channel === "sms" || ["rna", "busy", "failed", "canceled", "no-answer"].includes(status);
  const tl: CallEvent[] = [];
  if (!none) {
    const k = seed(id);
    let t = 0;
    const add = (dt: number, event: string, detail: Record<string, unknown> = {}) => {
      t += dt;
      tl.push({ ms: t, event, ...detail });
    };
    add(35, "prompt.rendered");
    add(90 + (k % 60), "media.start");
    if (status === "left_vmail") {
      add(2400, "amd.verdict", { verdict: "machine_end_beep", action: "voicemail", phase: "greeting" });
      add(300, "vm.play", { seconds: 18.4 });
      add(18_600, "call.ended", { reason: "voicemail_done" });
    } else {
      add(1600, "amd.verdict", { verdict: "human", action: "continue", phase: "greeting" });
      add(620 + (k % 400), "agent.audio_start");
      add(9_800, "tool.call", { name: "verify_identity" });
      add(310 + (k % 200), "tool.response", { name: "verify_identity", status: "verified" });
      add(21_000, "agent.interrupted");
      if (status === "interrupted") {
        add(14_000, "live.error", { code: "1011" });
        add(40, "call.ended", { reason: "live_error" });
      } else {
        add(64_000, "agent.interrupted");
        add(95_000, "tool.call", { name: "save_report" });
        add(480 + (k % 300), "tool.response", { name: "save_report", status: "ok" });
        add(20, "report.saved");
        add(5_200, "drain.mark_sent");
        add(1_900, "hangup.auto_after_report");
        add(60, "call.ended", { reason: "agent_hangup" });
      }
    }
  }
  const first = (e: string) => tl.find((x) => x.event === e)?.ms;
  const start = first("media.start");
  const audio = first("agent.audio_start");
  return {
    call_id: id,
    available: tl.length > 0,
    timeline: tl,
    summary: {
      events: tl.length,
      first_audio_ms: start != null && audio != null ? audio - start : null,
      tool_calls: tl.filter((e) => e.event === "tool.call").length,
      interruptions: tl.filter((e) => e.event === "agent.interrupted").length,
      errors: tl.filter((e) => e.event.startsWith("live.")).length,
    },
  };
}

/** A few fictional configuration changes, newest first. */
export function demoAudit(now: Date = new Date()): AuditEntry[] {
  const ago = (h: number) => new Date(now.getTime() - h * 3_600_000).toISOString();
  return [
    { id: "a5", at: ago(0.4), by: "jordan.admin@example.org", action: "PUT /api/call-settings/display", target: {} },
    { id: "a4", at: ago(2.1), by: "jordan.admin@example.org", action: "POST /api/allowlist", target: {} },
    { id: "a3", at: ago(5.5), by: "sam.lead@example.org", action: "DELETE /api/allowlist/{phone}", target: { phone: "+1202•••••0177" } },
    { id: "a2", at: ago(26), by: "sam.lead@example.org", action: "PATCH /api/call-runs/{run_id}", target: { run_id: "0000000000000000000000000000003" } },
    { id: "a1", at: ago(49), by: "jordan.admin@example.org", action: "PUT /api/call-settings/recall", target: {} },
  ];
}

/** On-demand reads over sample data; never touches the network. */
export function demoAdminReads(sessions: FollowUpSession[], calls: CallDoc[], now: Date = new Date()): AdminReads {
  return {
    async messages(phone, callSid) {
      const s = sessions.find((x) => x.phone === phone && (!callSid || x.call_sid === callSid));
      return s ? demoMessages(s) : [];
    },
    async callEvents(callId) {
      const c = calls.find((x) => (x.call_sid ?? x.doc_id) === callId || x.doc_id === callId);
      if (!c) return { call_id: callId, available: false, timeline: [], summary: { events: 0, first_audio_ms: null, tool_calls: 0, interruptions: 0, errors: 0 } };
      return demoCallEvents(c);
    },
    async audit() {
      return demoAudit(now);
    },
  };
}
