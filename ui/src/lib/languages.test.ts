import { describe, expect, it } from "vitest";
import {
  LanguagesResponse, callLanguage, holdReason, isAvailable, languageChip, normalizeLanguage, optionLabel, packChannelLines, parseYesNo,
} from "./languages";
import sample from "./languages-sample.json";

const RESP = sample as LanguagesResponse;
const LIST = RESP.languages;

describe("normalizeLanguage (same rules as voa_core.languages.normalize)", () => {
  it.each([
    ["es-US", "es-US"], ["es", "es-US"], ["ES-us", "es-US"], ["es_us", "es-US"], ["Spanish", "es-US"],
    ["spanish (us)", "es-US"], ["Español", "es-US"], ["en", "en-US"], ["English", "en-US"], ["en-IN", "en-IN"],
    ["pt", "pt-BR"], ["Arabic (Egyptian)", "ar-EG"], ["vi", "vi-VN"],
    ["zh", null], ["Mandarin", null], ["tl", null], ["es-MX", null], ["", null], [null, null],
  ] as const)("%s -> %s", (value, code) => {
    expect(normalizeLanguage(value, LIST)).toBe(code);
  });

  it("knows nothing without the list", () => {
    expect(normalizeLanguage("es", [])).toBeNull();
  });
});

describe("availability and the hold", () => {
  it("only English is available today", () => {
    expect(LIST.filter((l) => l.available).map((l) => l.code).sort()).toEqual(["en-IN", "en-US"]);
    expect(isAvailable("en-US", null, RESP)).toBe(true);
    expect(isAvailable("es-US", "pre_op", RESP)).toBe(false);
  });

  it("holds a language without support unless Call in English is on", () => {
    expect(holdReason({ language: "es-US" }, RESP)).toBe("Waiting for Spanish (US) support");
    expect(holdReason({ language: "es-US", call_in_english: true }, RESP)).toBeNull();
    expect(holdReason({ language: "en-US" }, RESP)).toBeNull();
    expect(holdReason({ language: null }, RESP)).toBeNull(); // older entries: English
    expect(holdReason({ language: "zh" }, RESP)).toMatch(/not supported/);
  });

  it("an approved pack (per use case) lifts the hold", () => {
    const withPack = { ...RESP, available_by_campaign: { ...RESP.available_by_campaign, post_op: ["en-IN", "en-US", "es-US"] } };
    expect(holdReason({ language: "es-US" }, withPack)).toBeNull();
    expect(holdReason({ language: "es-US", campaign_id: "pre_op" }, withPack)).toMatch(/Waiting/);
  });
});

describe("labels", () => {
  it("the chip is hidden for English (US) and names other languages natively", () => {
    expect(languageChip("en-US", LIST)).toBeNull();
    expect(languageChip(undefined, LIST)).toBeNull();
    expect(languageChip("es-US", LIST)).toBe("Español · Spanish (US)");
    expect(languageChip("en-IN", LIST)).toBe("English (India)");
    expect(languageChip("es-US", [])).toBe("es-US"); // list not loaded yet
  });

  it("option labels", () => {
    expect(optionLabel(LIST.find((l) => l.code === "vi-VN")!)).toBe("Vietnamese · Tiếng Việt");
    expect(optionLabel(LIST.find((l) => l.code === "en-US")!)).toBe("English (US)");
  });

  it("a call's language: the call doc, else its patient context", () => {
    expect(callLanguage({ language: "es-US" })).toBe("es-US");
    expect(callLanguage({ patient_context: { language: "vi-VN" } })).toBe("vi-VN");
    expect(callLanguage({})).toBeUndefined();
  });

  it("yes/no cells", () => {
    expect(parseYesNo("Yes")).toBe(true);
    expect(parseYesNo("")).toBe(false);
    expect(parseYesNo("no")).toBe(false);
    expect(parseYesNo("maybe")).toBeUndefined();
  });
});

describe("packChannelLines", () => {
  const base = {
    campaign_id: "post_op", language: "es-US", name: "Spanish (US)", status: "draft",
    reviewed_by: "", approved_by: "", approved_at: "",
    voice: { complete: true, available: false }, sms: { complete: false, available: false },
    needs: "needs translator review",
  };
  it("says a draft voice pack needs review and texts stay off", () => {
    expect(packChannelLines(base)).toEqual([
      "Voice: draft, needs translator review",
      "Texts: not translated yet (English texts are never sent instead)",
    ]);
  });
  it("says an approved voice pack is in use", () => {
    expect(packChannelLines({ ...base, status: "approved", needs: "", voice: { complete: true, available: true } })[0])
      .toBe("Voice: approved, in use");
  });
  it("matches the sample data", () => {
    const es = RESP.packs?.find((p) => p.campaign_id === "post_op" && p.language === "es-US");
    expect(es && packChannelLines(es)[0]).toBe("Voice: draft, needs translator review");
  });
});
