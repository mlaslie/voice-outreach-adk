import { CallListEntryInput, CareSetting } from "./types";
import { LanguageInfo, normalizeLanguage, parseYesNo } from "./languages";

/** Column order of the example CSV — also the canonical header set. */
export const CSV_COLUMNS = [
  "phone",
  "patient_name",
  "patient_id",
  "patient_dob",
  "operation_name",
  "operation_date",
  "is_diabetic",
  "care_setting",
  // Optional: a code or English name ("es-US", "Spanish"); blank is English (US).
  "language",
  // Optional: yes to call in English while the language is not available yet.
  "call_in_english",
] as const;

const REQUIRED = ["phone", "patient_name", "patient_id", "patient_dob"] as const;

export const EXAMPLE_CSV = [
  CSV_COLUMNS.join(","),
  "+12025550123,Jane Doe,P-1001,1950-03-15,Total knee replacement,2026-07-22,false,inpatient,,",
  "+12025550145,John Smith,P-1002,1962-11-04,Hip arthroplasty,2026-07-24,true,inpatient,en-US,no",
  '+12025550111,"Ruiz, Maria",P-1003,1978-06-30,Rotator cuff repair,2026-07-25,false,outpatient,Spanish,yes',
  "+12025550112,Nguyen An,P-1004,1955-01-09,Cataract surgery,2026-07-26,false,outpatient,vi-VN,",
].join("\n");

export class CsvError extends Error {}

/**
 * RFC-4180 scanner over the whole document, returning rows of fields.
 *
 * Scanning the full text (rather than splitting on newlines first) is what
 * lets a quoted field contain a comma, a doubled quote, OR a line break —
 * all three show up in real spreadsheet exports.
 */
function parseCsvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let inQuotes = false;
  let sawField = false;

  const endField = () => {
    row.push(cur.trim());
    cur = "";
    sawField = true;
  };
  const endRow = () => {
    endField();
    // Drop blank lines (a row of one empty field).
    if (!(row.length === 1 && row[0] === "")) rows.push(row);
    row = [];
    sawField = false;
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      endField();
    } else if (ch === "\r") {
      // swallow; the \n that follows ends the row (bare \r is rare enough)
    } else if (ch === "\n") {
      endRow();
    } else {
      cur += ch;
    }
  }
  if (cur !== "" || sawField || row.length) endRow();
  return rows;
}

/**
 * The optional care_setting column: inpatient / outpatient, "in" / "out",
 * any case; blank means not given (null). Anything else is `undefined`, an
 * error on that row.
 */
export function parseCareSetting(v: string | null | undefined): CareSetting | null | undefined {
  const k = (v ?? "").trim().toLowerCase();
  if (!k) return null;
  if (k === "inpatient" || k === "in") return "inpatient";
  if (k === "outpatient" || k === "out") return "outpatient";
  return undefined;
}

function parseBool(v: string): boolean {
  return ["true", "yes", "y", "1"].includes(v.trim().toLowerCase());
}

/** One data row of an import preview. `entry` is null when the row has errors. */
export interface CsvPreviewRow {
  /** 1-based line in the file, counting the header (row 2 is the first patient). */
  row: number;
  /** The raw cells by column name, for showing a rejected row as it was. */
  cells: Record<string, string>;
  entry: CallListEntryInput | null;
  errors: string[];
}

export interface CsvPreview {
  rows: CsvPreviewRow[];
  /** The rows without errors, in file order: what an import sends. */
  valid: CallListEntryInput[];
  /** How many rows have at least one error. */
  invalid: number;
}

const PHONE_RE = /^\+[1-9]\d{6,14}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const PATIENT_ID_RE = /^[A-Za-z0-9_.-]{1,64}$/;

/** "YYYY-MM-DD" that is a real calendar date. */
function isRealDate(v: string): boolean {
  if (!DATE_RE.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
}

/**
 * Parse a CSV and check every row, collecting ALL problems per row instead of
 * stopping at the first, so an admin can fix a file in one pass.
 *
 * File-level problems (empty file, missing required columns, no data rows)
 * still throw CsvError: there is nothing row-by-row to show.
 *
 * `existingPatientIds`: patient IDs already on the call list. A row that
 * repeats one is rejected, like a patient ID repeated within the file, so an
 * import can never put the same patient on the list (and the queue) twice.
 */
export function previewCallListCsv(
  text: string,
  existingPatientIds: Iterable<string> = [],
  /** GET /api/languages' list, to read the language column; null = not loaded. */
  languages: LanguageInfo[] | null = null,
): CsvPreview {
  // Excel and Google Sheets prefix UTF-8 exports with a BOM, which would
  // otherwise make the first header read "\ufeffphone" and fail the
  // required-column check on a file that looks perfectly correct.
  const rows = parseCsvRows(text.replace(/^\ufeff/, ""));

  if (rows.length === 0) throw new CsvError("The file is empty.");

  const header = rows[0].map((h) => h.toLowerCase());
  const missing = REQUIRED.filter((c) => !header.includes(c));
  if (missing.length) {
    throw new CsvError(
      `Missing required column${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}. ` +
        `Expected header: ${CSV_COLUMNS.join(",")}`
    );
  }
  if (rows.length === 1) throw new CsvError("The file has a header but no patient rows.");

  const existing = new Set(existingPatientIds);
  const seenPhones = new Map<string, number>();
  const seenIds = new Map<string, number>();
  const out: CsvPreviewRow[] = [];

  for (let r = 1; r < rows.length; r++) {
    const cells: Record<string, string> = {};
    header.forEach((h, i) => { cells[h] = rows[r][i] ?? ""; });
    const get = (name: string) => cells[name] ?? "";
    const rowNo = r + 1; // 1-based, counting the header
    const errors: string[] = [];

    const phone = get("phone");
    const patientName = get("patient_name");
    const patientId = get("patient_id");
    const dob = get("patient_dob");
    const opDate = get("operation_date");

    for (const [label, value] of [
      ["phone", phone],
      ["patient_name", patientName],
      ["patient_id", patientId],
      ["patient_dob", dob],
    ] as const) {
      if (!value) errors.push(`${label} is required.`);
    }
    // Same pattern as the backend's _E164_RE and the add-patient form, so a
    // number accepted in one place is never rejected in another.
    if (phone && !PHONE_RE.test(phone)) {
      errors.push(`phone must be E.164, e.g. +12025550123 (got "${phone}").`);
    } else if (phone) {
      const dupOf = seenPhones.get(phone);
      if (dupOf) {
        errors.push(`duplicate phone number — already listed on row ${dupOf}. Importing it twice would call the patient twice.`);
      } else {
        seenPhones.set(phone, rowNo);
      }
    }
    if (dob && !DATE_RE.test(dob)) {
      errors.push(`patient_dob must be YYYY-MM-DD (got "${dob}").`);
    } else if (dob && !isRealDate(dob)) {
      errors.push(`patient_dob is not a real date (got "${dob}").`);
    } else if (dob && dob > new Date().toISOString().slice(0, 10)) {
      errors.push("patient_dob is in the future.");
    }
    if (patientId && !PATIENT_ID_RE.test(patientId)) {
      errors.push(`patient_id may only contain letters, digits, "_", "." and "-".`);
    } else if (patientId) {
      const dupOf = seenIds.get(patientId);
      if (dupOf) errors.push(`duplicate patient_id — already used on row ${dupOf}.`);
      else seenIds.set(patientId, rowNo);
      if (existing.has(patientId)) errors.push(`patient_id ${patientId} is already on the patient list. Edit that patient instead.`);
    }
    if (opDate && !isRealDate(opDate)) {
      errors.push(`operation_date must be a real YYYY-MM-DD date (got "${opDate}").`);
    }
    const careSetting = parseCareSetting(get("care_setting"));
    if (careSetting === undefined) {
      errors.push(`care_setting must be inpatient, outpatient, in, out or blank (got "${get("care_setting")}").`);
    }
    const rawLanguage = get("language");
    const language = rawLanguage ? normalizeLanguage(rawLanguage, languages ?? []) : null;
    if (rawLanguage && !languages) {
      errors.push("The language list could not be loaded, so the language column cannot be checked. Reload and try again.");
    } else if (rawLanguage && !language) {
      errors.push(`language must be one of the 24 supported languages, as a code or English name, e.g. es-US or Spanish (got "${rawLanguage}").`);
    }
    const callInEnglish = parseYesNo(get("call_in_english"));
    if (callInEnglish === undefined) {
      errors.push(`call_in_english must be yes, no or blank (got "${get("call_in_english")}").`);
    }

    out.push({
      row: rowNo,
      cells,
      errors,
      entry: errors.length
        ? null
        : {
            phone,
            patient_name: patientName,
            patient_id: patientId,
            patient_dob: dob,
            operation_name: get("operation_name") || undefined,
            operation_date: opDate || undefined,
            is_diabetic: parseBool(get("is_diabetic")),
            ...(careSetting ? { care_setting: careSetting } : {}),
            ...(language ? { language } : {}),
            ...(callInEnglish ? { call_in_english: true } : {}),
          },
    });
  }

  return {
    rows: out,
    valid: out.flatMap((r) => (r.entry ? [r.entry] : [])),
    invalid: out.filter((r) => r.errors.length).length,
  };
}

/**
 * Parse a CSV into call-list entries. Throws CsvError with a row-numbered
 * message on the first problem, so the user gets a fixable error rather
 * than a silently-truncated import.
 */
export function parseCallListCsv(text: string, languages: LanguageInfo[] | null = null): CallListEntryInput[] {
  const preview = previewCallListCsv(text, [], languages);
  const bad = preview.rows.find((r) => r.errors.length);
  if (bad) throw new CsvError(`Row ${bad.row}: ${bad.errors[0]}`);
  return preview.valid;
}

/** The backend's limit per POST /api/call-list/bulk. */
export const BULK_LIMIT = 200;

export interface ImportResult {
  added: number;
  /** Rows the backend refused, with why (a whole chunk fails together). */
  rejected: { count: number; reason: string }[];
}

/**
 * Send entries in chunks of at most `size`, one after another. A failed
 * chunk does not stop the rest: its rows are reported as rejected with the
 * backend's message, so the summary says exactly what landed.
 */
export async function importInChunks(
  entries: CallListEntryInput[],
  send: (chunk: CallListEntryInput[]) => Promise<number>,
  size: number = BULK_LIMIT,
  onProgress?: (done: number, total: number) => void,
): Promise<ImportResult> {
  const result: ImportResult = { added: 0, rejected: [] };
  for (let i = 0; i < entries.length; i += size) {
    const chunk = entries.slice(i, i + size);
    try {
      result.added += await send(chunk);
    } catch (e) {
      result.rejected.push({ count: chunk.length, reason: e instanceof Error ? e.message : "Import refused." });
    }
    onProgress?.(Math.min(i + size, entries.length), entries.length);
  }
  return result;
}

export function downloadExampleCsv(): void {
  const blob = new Blob([EXAMPLE_CSV], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "call-list-example.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
