import { CallListEntryInput } from "./types";

/** Column order of the example CSV — also the canonical header set. */
export const CSV_COLUMNS = [
  "phone",
  "patient_name",
  "patient_id",
  "patient_dob",
  "operation_name",
  "operation_date",
  "is_diabetic",
] as const;

const REQUIRED = ["phone", "patient_name", "patient_id", "patient_dob"] as const;

export const EXAMPLE_CSV = [
  CSV_COLUMNS.join(","),
  "+12025550123,Jane Doe,P-1001,1950-03-15,Total knee replacement,2026-07-22,false",
  "+12025550145,John Smith,P-1002,1962-11-04,Hip arthroplasty,2026-07-24,true",
  '+12025550111,"Ruiz, Maria",P-1003,1978-06-30,Rotator cuff repair,2026-07-25,false',
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

function parseBool(v: string): boolean {
  return ["true", "yes", "y", "1"].includes(v.trim().toLowerCase());
}

/**
 * Parse a CSV into call-list entries. Throws CsvError with a row-numbered
 * message on the first problem, so the user gets a fixable error rather
 * than a silently-truncated import.
 */
export function parseCallListCsv(text: string): CallListEntryInput[] {
  // Excel and Google Sheets prefix UTF-8 exports with a BOM, which would
  // otherwise make the first header read "﻿phone" and fail the
  // required-column check on a file that looks perfectly correct.
  const rows = parseCsvRows(text.replace(/^﻿/, ""));

  if (rows.length === 0) throw new CsvError("The file is empty.");

  const header = rows[0].map((h) => h.toLowerCase());
  const missing = REQUIRED.filter((c) => !header.includes(c));
  if (missing.length) {
    throw new CsvError(
      `Missing required column${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}. ` +
        `Expected header: ${CSV_COLUMNS.join(",")}`
    );
  }

  const idx = (name: string) => header.indexOf(name);
  const entries: CallListEntryInput[] = [];
  const seenPhones = new Map<string, number>();

  for (let r = 1; r < rows.length; r++) {
    const cells = rows[r];
    const get = (name: string) => {
      const i = idx(name);
      return i >= 0 ? (cells[i] ?? "") : "";
    };
    const rowNo = r + 1; // 1-based, counting the header

    const phone = get("phone");
    const patientName = get("patient_name");
    const patientId = get("patient_id");
    const dob = get("patient_dob");

    for (const [label, value] of [
      ["phone", phone],
      ["patient_name", patientName],
      ["patient_id", patientId],
      ["patient_dob", dob],
    ] as const) {
      if (!value) throw new CsvError(`Row ${rowNo}: ${label} is required.`);
    }
    // Same pattern as the backend's _E164_RE and the add-patient form, so a
    // number accepted in one place is never rejected in another.
    if (!/^\+[1-9]\d{6,14}$/.test(phone)) {
      throw new CsvError(`Row ${rowNo}: phone must be E.164, e.g. +12025550123 (got "${phone}").`);
    }
    const dupOf = seenPhones.get(phone);
    if (dupOf) {
      throw new CsvError(
        `Row ${rowNo}: duplicate phone number — already listed on row ${dupOf}. ` +
          `Importing it twice would call the patient twice.`
      );
    }
    seenPhones.set(phone, rowNo);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dob)) {
      throw new CsvError(`Row ${rowNo}: patient_dob must be YYYY-MM-DD (got "${dob}").`);
    }
    if (!/^[A-Za-z0-9_.-]{1,64}$/.test(patientId)) {
      throw new CsvError(
        `Row ${rowNo}: patient_id may only contain letters, digits, "_", "." and "-".`
      );
    }

    entries.push({
      phone,
      patient_name: patientName,
      patient_id: patientId,
      patient_dob: dob,
      operation_name: get("operation_name") || undefined,
      operation_date: get("operation_date") || undefined,
      is_diabetic: parseBool(get("is_diabetic")),
    });
  }

  if (entries.length === 0) {
    throw new CsvError("The file has a header but no patient rows.");
  }
  return entries;
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
