import { CallDoc, TranscriptTurn } from "./types";
import { formatDuration, formatPhone } from "./format";
import { getStatusLabel } from "./status";
import { fmtClock, fmtDateTime } from "./time";
import { TranscriptView, hasTranslation, turnText } from "./translation";

/** Turn time with seconds, in the clinic's 12h/24h setting. */
function stamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return fmtClock(d, { seconds: true });
}

const FULL_DATE = { weekday: "short", month: "short", day: "numeric", year: "numeric" } as const;

/**
 * Slug safe for a filename, derived from the patient or the call SID. Accents
 * fold to plain letters ("José" → "jose"); letters of other scripts are kept
 * ("王芳" stays), so a non-Latin name no longer collapses to nothing.
 */
export function transcriptFilename(call?: CallDoc | null, view?: TranscriptView): string {
  const who = (call?.patient_name || call?.patient_id || call?.doc_id || "call")
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "") || "call";
  const when = call?.initiated_at ? call.initiated_at.slice(0, 10) : "transcript";
  const suffix = view === "original" ? "-original" : view === "side" ? "-bilingual" : "";
  return `transcript-${who}-${when}${suffix}.txt`;
}

export interface TranscriptTextOptions {
  /** The use case's display title ("Post-op check"); absent = a plain title. */
  title?: string;
  /** Which words to write. Only matters when the transcript was translated. */
  view?: TranscriptView;
  /** The language the transcript was translated from, as a name ("Spanish (US)"). */
  languageName?: string | null;
}

/** The download's title line, with the language noted when it matters. */
export function transcriptTitle(opts: TranscriptTextOptions = {}, translated = false): string {
  const base = opts.title ? `${opts.title} — Call Transcript` : "Call Transcript";
  if (!translated) return base;
  const from = opts.languageName ? ` from ${opts.languageName}` : "";
  if (opts.view === "original") return `${base} (original language${opts.languageName ? `: ${opts.languageName}` : ""})`;
  if (opts.view === "side") return `${base} (original and English, machine-translated${from})`;
  return `${base} (English, machine-translated${from})`;
}

/**
 * Render a transcript as plain text with a metadata header.
 *
 * The phone number is masked exactly as it is on screen — the dashboard
 * never shows the full number, and a downloaded file shouldn't be the
 * one place it leaks.
 */
export function transcriptToText(
  call: CallDoc | null | undefined,
  turns: TranscriptTurn[],
  opts: TranscriptTextOptions = {},
): string {
  const translated = hasTranslation(turns);
  const view: TranscriptView = translated ? opts.view ?? "english" : "original";
  const lines: string[] = [transcriptTitle(opts, translated), ""];

  const meta: [string, string | undefined][] = [
    ["Patient", call?.patient_name],
    ["Patient ID", call?.patient_id],
    ["Phone", call?.to ? formatPhone(call.to) : undefined],
    ["Call SID", call?.doc_id],
    ["Started", call?.initiated_at ? fmtDateTime(call.initiated_at, FULL_DATE) : undefined],
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
      const head = `[${stamp(t.timestamp)}] ${who}: `;
      if (view === "side") {
        lines.push(`${head}${t.text}`);
        if (t.text_en) lines.push(`${" ".repeat(head.length - 5)}[EN] ${t.text_en}`);
      } else {
        lines.push(`${head}${turnText(t, view)}`);
      }
    }
  }

  lines.push("", `Exported ${fmtDateTime(new Date(), FULL_DATE)}`);
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
