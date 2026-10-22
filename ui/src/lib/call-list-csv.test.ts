import { describe, expect, it } from "vitest";
import {
  BULK_LIMIT, CsvError, EXAMPLE_CSV, importInChunks, parseCallListCsv, parseCareSetting, previewCallListCsv,
} from "./call-list-csv";
import { LanguagesResponse } from "./languages";
import sample from "./languages-sample.json";

const LANGS = (sample as LanguagesResponse).languages;

const HEADER = "phone,patient_name,patient_id,patient_dob,operation_name,operation_date,is_diabetic";
const ROW = "+12025550123,Jane Doe,P-1001,1950-03-15,Total knee replacement,2026-07-22,false";

describe("parseCallListCsv", () => {
  it("parses the example CSV we hand users", () => {
    const rows = parseCallListCsv(EXAMPLE_CSV, LANGS);
    expect(rows).toHaveLength(4);
    expect(rows[2].patient_name).toBe("Ruiz, Maria"); // quoted comma survives
    expect(rows[1].is_diabetic).toBe(true);
    expect(rows[0].is_diabetic).toBe(false);
    expect(rows.map((r) => r.care_setting)).toEqual(["inpatient", "inpatient", "outpatient", "outpatient"]);
    expect(rows.map((r) => r.language)).toEqual([undefined, "en-US", "es-US", "vi-VN"]);
    expect(rows.map((r) => r.call_in_english)).toEqual([undefined, undefined, true, undefined]);
  });

  it("accepts CRLF line endings", () => {
    const rows = parseCallListCsv(`${HEADER}\r\n${ROW}\r\n`);
    expect(rows).toHaveLength(1);
    expect(rows[0].phone).toBe("+12025550123");
  });

  it("strips the UTF-8 BOM that Excel and Sheets prepend", () => {
    const rows = parseCallListCsv(`﻿${HEADER}\n${ROW}`);
    expect(rows).toHaveLength(1);
    expect(rows[0].patient_id).toBe("P-1001");
  });

  it("handles BOM and CRLF together", () => {
    const rows = parseCallListCsv(`﻿${HEADER}\r\n${ROW}\r\n`);
    expect(rows).toHaveLength(1);
  });

  it("keeps a newline inside a quoted field", () => {
    const rows = parseCallListCsv(
      `${HEADER}\n"+12025550123","Jane\nDoe",P-1001,1950-03-15,Knee,2026-07-22,false`
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].patient_name).toBe("Jane\nDoe");
  });

  it("unescapes doubled quotes", () => {
    const rows = parseCallListCsv(
      `${HEADER}\n+12025550123,"Jane ""JD"" Doe",P-1001,1950-03-15,,,false`
    );
    expect(rows[0].patient_name).toBe('Jane "JD" Doe');
  });

  it("ignores blank lines and trailing newlines", () => {
    const rows = parseCallListCsv(`${HEADER}\n\n${ROW}\n\n`);
    expect(rows).toHaveLength(1);
  });

  it("leaves optional columns undefined when empty", () => {
    const rows = parseCallListCsv(`${HEADER}\n+12025550123,Jane Doe,P-1001,1950-03-15,,,`);
    expect(rows[0].operation_name).toBeUndefined();
    expect(rows[0].operation_date).toBeUndefined();
    expect(rows[0].is_diabetic).toBe(false);
  });

  it("accepts a 7-digit E.164 number, matching the backend regex", () => {
    const rows = parseCallListCsv(`${HEADER}\n+1555123,Jane Doe,P-1001,1950-03-15,,,false`);
    expect(rows[0].phone).toBe("+1555123");
  });

  describe("rejections", () => {
    it("names the missing required column", () => {
      expect(() => parseCallListCsv("phone,patient_name\n+12025550123,Jane"))
        .toThrow(/Missing required column/);
    });

    it("reports the row number for a bad phone", () => {
      expect(() => parseCallListCsv(`${HEADER}\n2025550123,Jane Doe,P-1001,1950-03-15,,,false`))
        .toThrow(/Row 2: phone must be E.164/);
    });

    it("reports the row number for a bad DOB", () => {
      expect(() => parseCallListCsv(`${HEADER}\n+12025550123,Jane Doe,P-1001,03/15/1950,,,false`))
        .toThrow(/Row 2: patient_dob must be YYYY-MM-DD/);
    });

    it("rejects an illegal patient_id charset", () => {
      expect(() => parseCallListCsv(`${HEADER}\n+12025550123,Jane Doe,P 1001!,1950-03-15,,,false`))
        .toThrow(/Row 2: patient_id/);
    });

    it("rejects a duplicate phone rather than calling someone twice", () => {
      expect(() => parseCallListCsv(`${HEADER}\n${ROW}\n${ROW}`))
        .toThrow(/Row 3: duplicate phone number — already listed on row 2/);
    });

    it("rejects a header with no data rows", () => {
      expect(() => parseCallListCsv(HEADER)).toThrow(/no patient rows/);
    });

    it("rejects an empty file", () => {
      expect(() => parseCallListCsv("")).toThrow(CsvError);
    });
  });
});

describe("previewCallListCsv: every row checked before anything is sent", () => {
  const H = "phone,patient_name,patient_id,patient_dob,operation_name,operation_date,is_diabetic";

  it("keeps good rows and lists every error on a bad one", () => {
    const p = previewCallListCsv([
      H,
      "+12025550123,Jane Doe,P-1001,1950-03-15,Knee,2026-07-22,false",
      "2025550123,,P 1!,03/15/1950,,,",
      "+12025550145,John Smith,P-1002,1962-11-04,,,true",
    ].join("\n"));
    expect(p.valid.map((e) => e.patient_id)).toEqual(["P-1001", "P-1002"]);
    expect(p.invalid).toBe(1);
    const bad = p.rows[1];
    expect(bad.row).toBe(3);
    expect(bad.entry).toBeNull();
    expect(bad.cells.phone).toBe("2025550123");
    expect(bad.errors.join(" | ")).toMatch(/patient_name is required/);
    expect(bad.errors.join(" | ")).toMatch(/phone must be E\.164/);
    expect(bad.errors.join(" | ")).toMatch(/patient_dob must be YYYY-MM-DD/);
    expect(bad.errors.join(" | ")).toMatch(/patient_id may only contain/);
  });

  it("rejects a duplicate patient_id in the file, and one already on the list", () => {
    const p = previewCallListCsv([
      H,
      "+12025550123,Jane Doe,P-1001,1950-03-15,,,",
      "+12025550124,Jane Again,P-1001,1950-03-15,,,",
      "+12025550125,Old Patient,P-0009,1950-03-15,,,",
    ].join("\n"), ["P-0009"]);
    expect(p.rows[1].errors[0]).toMatch(/duplicate patient_id — already used on row 2/);
    expect(p.rows[2].errors[0]).toMatch(/already on the patient list/);
    expect(p.valid).toHaveLength(1);
  });

  it("rejects a date that is not on the calendar, a future DOB and a bad operation date", () => {
    const p = previewCallListCsv([
      H,
      "+12025550123,A,P-1,1950-02-30,,,",
      "+12025550124,B,P-2,2999-01-01,,,",
      "+12025550125,C,P-3,1950-01-01,,2026-13-01,",
    ].join("\n"));
    expect(p.rows[0].errors[0]).toMatch(/not a real date/);
    expect(p.rows[1].errors[0]).toMatch(/in the future/);
    expect(p.rows[2].errors[0]).toMatch(/operation_date/);
  });

  it("still throws for file-level problems", () => {
    expect(() => previewCallListCsv("")).toThrow(CsvError);
    expect(() => previewCallListCsv(H)).toThrow(/no patient rows/);
    expect(() => previewCallListCsv("phone\n+12025550123")).toThrow(/Missing required columns/);
  });
});

describe("importInChunks", () => {
  const entry = (i: number) => ({
    phone: `+1202555${String(i).padStart(4, "0")}`, patient_name: `P${i}`, patient_id: `P-${i}`,
    patient_dob: "1950-01-01", is_diabetic: false,
  });

  it("sends at most BULK_LIMIT rows per request", async () => {
    const sizes: number[] = [];
    const rows = Array.from({ length: 450 }, (_, i) => entry(i));
    const r = await importInChunks(rows, async (c) => { sizes.push(c.length); return c.length; });
    expect(BULK_LIMIT).toBe(200);
    expect(sizes).toEqual([200, 200, 50]);
    expect(r).toEqual({ added: 450, rejected: [] });
  });

  it("reports a refused chunk as rejected and carries on", async () => {
    const rows = Array.from({ length: 5 }, (_, i) => entry(i));
    let n = 0;
    const progress: number[] = [];
    const r = await importInChunks(rows, async (c) => {
      if (++n === 2) throw new Error("Call list is full");
      return c.length;
    }, 2, (done) => progress.push(done));
    expect(r.added).toBe(3);
    expect(r.rejected).toEqual([{ count: 2, reason: "Call list is full" }]);
    expect(progress).toEqual([2, 4, 5]);
  });
});

describe("care_setting", () => {
  it("accepts inpatient, outpatient, in, out and blank, in any case", () => {
    expect(parseCareSetting("inpatient")).toBe("inpatient");
    expect(parseCareSetting(" IN ")).toBe("inpatient");
    expect(parseCareSetting("Outpatient")).toBe("outpatient");
    expect(parseCareSetting("out")).toBe("outpatient");
    expect(parseCareSetting("")).toBeNull();
    expect(parseCareSetting("  ")).toBeNull();
    expect(parseCareSetting(undefined)).toBeNull();
    expect(parseCareSetting("day surgery")).toBeUndefined();
    expect(parseCareSetting("i")).toBeUndefined();
  });

  it("sends the column only when given, and rejects other values on the row", () => {
    const H = `${HEADER},care_setting`;
    const p = previewCallListCsv([
      H,
      `${ROW},OUT`,
      "+12025550145,John Smith,P-1002,1962-11-04,Hip arthroplasty,2026-07-24,true,",
      "+12025550146,Ann Lee,P-1003,1962-11-04,Hip arthroplasty,2026-07-24,true,home",
    ].join("\n"));
    expect(p.rows[0].entry?.care_setting).toBe("outpatient");
    expect(p.rows[1].entry).not.toHaveProperty("care_setting");
    expect(p.rows[2].entry).toBeNull();
    expect(p.rows[2].errors.join(" ")).toMatch(/care_setting must be inpatient, outpatient, in, out or blank \(got "home"\)/);
  });

  it("is optional: a file without the column imports as before", () => {
    expect(parseCallListCsv(`${HEADER}\n${ROW}`)[0]).not.toHaveProperty("care_setting");
  });
});

describe("the language columns", () => {
  const H = "phone,patient_name,patient_id,patient_dob,language,call_in_english";
  const row = (lang: string, eng = "") => `+12025550123,Ana Ruiz,P-1,1950-03-15,${lang},${eng}`;

  it("accepts codes and English names, any case, and normalises them", () => {
    for (const [cell, code] of [["es-US", "es-US"], ["ES-us", "es-US"], ["es", "es-US"], ["Spanish", "es-US"],
      ["spanish (us)", "es-US"], ["Vietnamese", "vi-VN"], ["en", "en-US"], ["en-IN", "en-IN"]] as const) {
      const p = previewCallListCsv(`${H}\n${row(cell)}`, [], LANGS);
      expect(p.rows[0].errors).toEqual([]);
      expect(p.valid[0].language).toBe(code);
    }
  });

  it("a blank language is English (US), sent as nothing", () => {
    const p = previewCallListCsv(`${H}\n${row("")}`, [], LANGS);
    expect(p.valid[0].language).toBeUndefined();
  });

  it("an unsupported language or a bad yes/no is a row error", () => {
    const second = "+12025550124,Ben Cho,P-2,1950-03-15,es,maybe";
    const p = previewCallListCsv(`${H}\n${row("zh")}\n${second}`, [], LANGS);
    expect(p.rows[1].errors).toHaveLength(1);
    expect(p.rows[0].errors.join()).toMatch(/language must be one of the 24/);
    expect(p.rows[1].errors.join()).toMatch(/call_in_english must be yes, no or blank/);
    expect(p.invalid).toBe(2);
  });

  it("call_in_english yes is sent; no is not", () => {
    const yes = previewCallListCsv(`${H}\n${row("es", "yes")}`, [], LANGS);
    expect(yes.valid[0].call_in_english).toBe(true);
    const no = previewCallListCsv(`${H}\n${row("es", "No")}`, [], LANGS);
    expect(no.valid[0].call_in_english).toBeUndefined();
  });

  it("a language cell without the list loaded is a row error, never sent unchecked", () => {
    const p = previewCallListCsv(`${H}\n${row("es")}`, [], null);
    expect(p.rows[0].errors.join()).toMatch(/could not be loaded/);
    expect(previewCallListCsv(`${H}\n${row("")}`, [], null).invalid).toBe(0);
  });
});
