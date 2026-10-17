import { describe, expect, it } from "vitest";
import { CsvError, EXAMPLE_CSV, parseCallListCsv } from "./call-list-csv";

const HEADER = "phone,patient_name,patient_id,patient_dob,operation_name,operation_date,is_diabetic";
const ROW = "+12025550123,Jane Doe,P-1001,1950-03-15,Total knee replacement,2026-07-22,false";

describe("parseCallListCsv", () => {
  it("parses the example CSV we hand users", () => {
    const rows = parseCallListCsv(EXAMPLE_CSV);
    expect(rows).toHaveLength(3);
    expect(rows[2].patient_name).toBe("Ruiz, Maria"); // quoted comma survives
    expect(rows[1].is_diabetic).toBe(true);
    expect(rows[0].is_diabetic).toBe(false);
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
