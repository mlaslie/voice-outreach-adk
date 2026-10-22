import { describe, expect, it } from "vitest";
import {
  AUTO_APPROVE_BANNER, LanguagesResponse, WordingSource, callInEnglishHint, callLanguage, holdReason, isAutoApproved,
  isAvailable, languageChip, languageOptionSuffix, languageSource, normalizeLanguage, optionLabel, packChannelLines,
  parseYesNo, spokeAutoApprovedLabel, textTestWording, unreviewedCounts, wordingSourceLabel,
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
  it("says drafted texts need review", () => {
    expect(packChannelLines({ ...base, sms: { complete: true, available: false } })[1])
      .toBe("Texts: draft, needs translator review");
    expect(packChannelLines({ ...base, sms: { complete: true, available: true } })[1])
      .toBe("Texts: approved, in use");
  });
  it("matches the sample data", () => {
    const es = RESP.packs?.find((p) => p.campaign_id === "post_op" && p.language === "es-US");
    expect(es && packChannelLines(es)).toEqual([
      "Voice: draft, needs translator review",
      "Texts: draft, needs translator review",
    ]);
  });
});

describe("textTestWording", () => {
  it("allows English, labels a draft, refuses a language with no text wording", () => {
    expect(textTestWording("en-US", RESP.packs)).toEqual({ approved: true, label: "" });
    expect(textTestWording("es-US", RESP.packs)).toEqual({
      approved: false, label: "Draft wording, not approved",
    });
    expect(textTestWording("vi-VN", RESP.packs)).toBeNull();
  });
});

// The demo switch (languages.auto_approve): every code available, wording
// approved for English, a draft pack for es-US, machine-translated otherwise.
const srcOf = (code: string): WordingSource =>
  code.startsWith("en-") ? "approved" : code === "es-US" ? "draft_pack" : "machine";
const ALL = LIST.map((l) => l.code);
const OFF: LanguagesResponse = { ...RESP, auto_approved: false };
const AUTO: LanguagesResponse = {
  ...RESP,
  auto_approved: true,
  languages: LIST.map((l) => ({ ...l, available: true, source: srcOf(l.code), sms_source: srcOf(l.code) })),
  available_by_campaign: Object.fromEntries(Object.keys(RESP.available_by_campaign).map((c) => [c, ALL])),
};

describe("auto-approve (demo)", () => {
  it("the banner text is exact", () => {
    expect(AUTO_APPROVE_BANNER).toBe(
      "Demo: all languages auto-approved. Wording in languages without an approved pack is machine-translated and unreviewed. Not for real patients.",
    );
  });

  it("isAutoApproved: absent or false is off", () => {
    expect(isAutoApproved(AUTO)).toBe(true);
    expect(isAutoApproved(OFF)).toBe(false);
    const { auto_approved: _drop, ...older } = AUTO;
    void _drop;
    expect(isAutoApproved(older as LanguagesResponse)).toBe(false);
    expect(isAutoApproved(null)).toBe(false);
  });

  it("languageSource: per use case map first, else the list entry; English is approved", () => {
    expect(languageSource("en-US", "post_op", null)).toBe("approved");
    expect(languageSource("en-IN", null, OFF)).toBe("approved");
    expect(languageSource("vi-VN", "post_op", AUTO)).toBe("machine");
    expect(languageSource("es-US", "post_op", AUTO, "sms")).toBe("draft_pack");
    const mapped: LanguagesResponse = {
      ...AUTO,
      sources_by_campaign: { pre_op: { "es-US": { voice: "machine", sms: null } } },
    };
    expect(languageSource("es-US", "pre_op", mapped)).toBe("machine");
    expect(languageSource("es-US", "pre_op", mapped, "sms")).toBeNull();
    expect(languageSource("es-US", "post_op", mapped)).toBe("draft_pack"); // not in the map: the list
    expect(languageSource("vi-VN", "post_op", OFF)).toBeNull(); // older server: unknown
  });

  it("option suffix: off is unchanged, on never says not available", () => {
    expect(languageOptionSuffix("vi-VN", "post_op", OFF)).toBe(" (not available yet)");
    expect(languageOptionSuffix("en-US", "post_op", OFF)).toBe("");
    expect(languageOptionSuffix("vi-VN", "post_op", RESP)).toBe(" (not available yet)");
    for (const code of ALL) {
      expect(languageOptionSuffix(code, "post_op", AUTO)).not.toMatch(/not available/);
    }
    expect(languageOptionSuffix("vi-VN", "post_op", AUTO)).toBe(" (auto-approved, demo)");
    expect(languageOptionSuffix("es-US", "pre_op", AUTO)).toBe(" (auto-approved, demo)");
    expect(languageOptionSuffix("en-US", "post_op", AUTO)).toBe("");
  });

  it("holdReason: nobody is held when auto-approved; unchanged when off", () => {
    expect(holdReason({ language: "vi-VN" }, AUTO)).toBeNull();
    expect(holdReason({ language: "es-US", campaign_id: "pre_op" }, AUTO)).toBeNull();
    expect(holdReason({ language: "vi-VN" }, OFF)).toBe("Waiting for Vietnamese support");
    expect(holdReason({ language: "zh" }, AUTO)).toMatch(/not supported/);
    expect(callInEnglishHint("vi-VN", LIST)).toMatch(/^Calls in Vietnamese aren't available yet/);
  });

  it("wordingSourceLabel and the counts", () => {
    expect(wordingSourceLabel("approved")).toBe("");
    expect(wordingSourceLabel(null)).toBe("");
    expect(wordingSourceLabel("draft_pack")).toBe("auto-approved, unreviewed wording");
    expect(wordingSourceLabel("machine")).toBe("auto-approved, unreviewed wording");
    const n = ALL.filter((c) => srcOf(c) === "machine").length;
    expect(unreviewedCounts(AUTO)).toEqual({ machine: n, draft_pack: 1 });
    expect(unreviewedCounts(OFF)).toEqual({ machine: 0, draft_pack: 0 });
  });

  it("spokeAutoApprovedLabel names the language of an unreviewed call", () => {
    expect(spokeAutoApprovedLabel({ language: "vi-VN", language_used: "vi-VN", language_source: "machine" }, LIST))
      .toBe("Spoke Vietnamese (auto-approved, unreviewed wording)");
    // Spoken English on a Spanish call: the call's language.
    expect(spokeAutoApprovedLabel({ language: "es-US", language_used: "en-US", language_source: "draft_pack" }, LIST))
      .toBe("Spoke Spanish (US) (auto-approved, unreviewed wording)");
    // Not translated yet: the patient context's language.
    expect(spokeAutoApprovedLabel({ patient_context: { language: "vi-VN" }, language_source: "machine" }, LIST))
      .toBe("Spoke Vietnamese (auto-approved, unreviewed wording)");
    expect(spokeAutoApprovedLabel({ language: "vi-VN", language_source: "approved" }, LIST)).toBeNull();
    expect(spokeAutoApprovedLabel({ language: "vi-VN", language_used: "vi-VN" }, LIST)).toBeNull();
    expect(spokeAutoApprovedLabel({ language: "en-US", language_source: "machine" }, LIST)).toBeNull();
  });

  it("textTestWording: unchanged when off; machine-translated when auto-approved", () => {
    expect(textTestWording("vi-VN", RESP.packs, "post_op", OFF)).toBeNull();
    expect(textTestWording("vi-VN", RESP.packs, "post_op", AUTO)).toEqual({
      approved: false, label: "Machine-translated, unreviewed (demo)",
    });
    expect(textTestWording("es-US", RESP.packs, "post_op", AUTO)).toEqual({
      approved: false, label: "Draft wording, not approved",
    });
    expect(textTestWording("en-US", RESP.packs, "post_op", AUTO)).toEqual({ approved: true, label: "" });
  });
});
