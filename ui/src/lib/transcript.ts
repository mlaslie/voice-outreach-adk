import { CallDoc, TranscriptTurn } from "./types";
import { formatDuration, formatPhone } from "./format";
import { getStatusLabel } from "./status";

function stamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString([], { hour12: false });
}

/** Slug safe for a filename, derived from the patient or the call SID. */
export function transcriptFilename(call?: CallDoc | null): string {
  const who = (call?.patient_name || call?.patient_id || call?.doc_id || "call")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  const when = call?.initiated_at ? call.initiated_at.slice(0, 10) : "transcript";
  return `transcript-${who}-${when}.txt`;
}

/**
 * Render a transcript as plain text with a metadata header.
 *
 * The phone number is masked exactly as it is on screen — the dashboard
 * never shows the full number, and a downloaded file shouldn't be the
 * one place it leaks.
 */
export function transcriptToText(call: CallDoc | null | undefined, turns: TranscriptTurn[]): string {
  const lines: string[] = ["Post-Op Wellness Check — Call Transcript", ""];

  const meta: [string, string | undefined][] = [
    ["Patient", call?.patient_name],
    ["Patient ID", call?.patient_id],
    ["Phone", call?.to ? formatPhone(call.to) : undefined],
    ["Call SID", call?.doc_id],
    ["Started", call?.initiated_at ? new Date(call.initiated_at).toLocaleString() : undefined],
    ["Duration", call?.duration_seconds != null ? formatDuration(call.duration_seconds) : undefined],
    ["Status", call ? getStatusLabel(call.call_status) : undefined],
    ["Outcome", call?.outcome],
    ["DOB check", call?.dob_verification_result],
    ["Follow-up", call?.follow_up],
    ["Follow-up reason", call?.follow_up_reason],
  ];

  const width = Math.max(...meta.filter(([, v]) => v).map(([k]) => k.length), 0);
  for (const [k, v] of meta) {
    if (v) lines.push(`${k.padEnd(width)} : ${v}`);
  }

  lines.push("", "-".repeat(60), "");

  if (turns.length === 0) {
    lines.push("(no transcript turns available)");
  } else {
    for (const t of turns) {
      const who = t.role === "agent" ? "Agent  " : "Patient";
      lines.push(`[${stamp(t.timestamp)}] ${who}: ${t.text}`);
    }
  }

  lines.push("", `Exported ${new Date().toLocaleString()}`);
  return lines.join("\n");
}

/** Trigger a browser download of a text file. */
export function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking synchronously can race the browser's download manager (Firefox
  // and Safari have both shipped 0-byte downloads this way). Let the current
  // task finish first.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
