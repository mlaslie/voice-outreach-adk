import { describe, expect, it } from "vitest";
import {
  hasTranslation, looksEnglish, messageText, sourceLanguage, spokeChip, translatedLabel,
  translationNote, translationState, turnText,
} from "./translation";
import sample from "./languages-sample.json";
import { LanguagesResponse } from "./languages";

const LIST = (sample as LanguagesResponse).languages;

const es = [
  { text: "Sí, soy yo.", text_en: "Yes, it's me.", lang: "es-US" },
  { text: "OK", text_en: null, lang: "en-US" },
];
const english = [{ text: "Hi, is this Jordan?" }, { text: "Yes" }];

describe("translation state (what the toggle and the note show)", () => {
  it("translated when any turn has English", () => {
    expect(translationState(es, { status: "done" })).toBe("translated");
    expect(hasTranslation(es)).toBe(true);
  });
  it("an all-English call shows nothing new, translated or not", () => {
    expect(translationState(english, null)).toBe("none");
    expect(translationState(english, { status: "skipped" })).toBe("none");
    expect(translationNote("none", null, LIST)).toBeNull();
  });
  it("pending: not run yet and something is not English", () => {
    expect(translationState([{ text: "Нет" }], null)).toBe("pending");
    expect(translationState([{ text: "no tengo fiebre" }], undefined)).toBe("pending");
    expect(translationNote("pending", null, LIST)).toMatch(/^Translating…/);
  });
  it("failed: the original with a note", () => {
    expect(translationState([{ text: "Нет" }], { status: "failed" })).toBe("failed");
    expect(translationNote("failed", null, LIST)).toMatch(/failed/);
  });
});

describe("the toggle", () => {
  it("English falls back to the original for untranslated turns", () => {
    expect(turnText(es[0], "english")).toBe("Yes, it's me.");
    expect(turnText(es[1], "english")).toBe("OK");
    expect(turnText(es[0], "original")).toBe("Sí, soy yo.");
    expect(turnText(es[0], "side")).toBe("Yes, it's me.");
  });
  it("texts the same way", () => {
    expect(messageText({ body: "Me siento bien", body_en: "I feel fine" }, "english")).toBe("I feel fine");
    expect(messageText({ body: "Me siento bien", body_en: "I feel fine" }, "original")).toBe("Me siento bien");
    expect(messageText({ body: "pretty good" }, "english")).toBe("pretty good");
  });
});

describe("labels", () => {
  it("names the source language", () => {
    expect(sourceLanguage(es, "es-US")).toBe("es-US");
    expect(sourceLanguage(es, "en-US")).toBe("es-US"); // a mostly-English call with Spanish turns
    expect(sourceLanguage(es)).toBe("es-US");
    expect(sourceLanguage([{ text_en: null, lang: "en-US" }])).toBeNull();
    expect(translatedLabel("es-US", LIST)).toBe("Machine-translated from Spanish (US)");
    expect(translationNote("translated", "vi-VN", LIST)).toBe("Machine-translated from Vietnamese");
  });
  it("the Spoke chip only when the language differs", () => {
    expect(spokeChip(undefined, "es-US", LIST)).toBe("Spoke Spanish (US)");
    expect(spokeChip("en-US", "es-US", LIST)).toBe("Spoke Spanish (US)");
    expect(spokeChip("es-US", "en-US", LIST)).toBe("Spoke English (US)");
    expect(spokeChip("es-US", "es-US", LIST)).toBeNull();
    expect(spokeChip("en-IN", "en-US", LIST)).toBeNull();
    expect(spokeChip(undefined, "en-US", LIST)).toBeNull();
    expect(spokeChip("es-US", undefined, LIST)).toBeNull(); // not translated yet
    expect(spokeChip(undefined, "zh", LIST)).toBe("Spoke zh"); // not one of the 24: the code
  });
});

describe("looksEnglish (not a safety gate)", () => {
  it.each(["Yes", "7", "I don't know", "OK, thanks"])("%s", (t) => expect(looksEnglish(t)).toBe(true));
  it.each(["Sí", "tengo dolor", "Не знаю", "我不知道", "Không"])("%s", (t) => expect(looksEnglish(t)).toBe(false));
});
