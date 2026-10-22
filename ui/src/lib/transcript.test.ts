import { afterEach, describe, expect, it } from "vitest";
import { transcriptFilename, transcriptTitle, transcriptToText } from "./transcript";
import { setTimeFormat } from "./time";
import { CallDoc, TranscriptTurn } from "./types";

// A local wall-clock instant, so the stamp is the same in any test zone.
const at = new Date(2026, 9, 19, 14, 30, 5).toISOString();
const turns = [{ role: "agent", text: "Hello, this is a test call.", timestamp: at }] as TranscriptTurn[];
const es = [
  { role: "agent", text: "¿Tiene fiebre?", text_en: "Do you have a fever?", lang: "es-US", timestamp: at },
  { role: "patient", text: "No sé.", text_en: "I don't know.", lang: "es-US", timestamp: at },
] as TranscriptTurn[];

afterEach(() => setTimeFormat("24h"));

describe("transcript export follows the time display setting", () => {
  it("24-hour", () => {
    setTimeFormat("24h");
    expect(transcriptToText(null, turns)).toContain("[14:30:05] Agent  : Hello");
  });
  it("12-hour", () => {
    setTimeFormat("12h");
    expect(transcriptToText(null, turns)).toContain("[2:30:05 PM] Agent  : Hello");
  });
});

describe("the download's title and language", () => {
  it("uses the use case's title, with no language note for an English call", () => {
    const text = transcriptToText(null, turns, { title: "Post-op check" });
    expect(text.split("\n")[0]).toBe("Post-op check — Call Transcript");
    expect(transcriptToText(null, turns).split("\n")[0]).toBe("Call Transcript");
  });
  it("English by default, with the language on the title line", () => {
    const text = transcriptToText(null, es, { title: "Post-op check", languageName: "Spanish (US)" });
    expect(text.split("\n")[0]).toBe("Post-op check — Call Transcript (English, machine-translated from Spanish (US))");
    expect(text).toContain("Patient: I don't know.");
    expect(text).not.toContain("No sé.");
  });
  it("the original", () => {
    const text = transcriptToText(null, es, { view: "original", languageName: "Spanish (US)" });
    expect(text.split("\n")[0]).toBe("Call Transcript (original language: Spanish (US))");
    expect(text).toContain("Patient: No sé.");
    expect(text).not.toContain("I don't know.");
  });
  it("side by side writes both", () => {
    const text = transcriptToText(null, es, { view: "side" });
    expect(text).toContain("Patient: No sé.");
    expect(text).toContain("[EN] I don't know.");
    expect(transcriptTitle({ view: "side" }, true)).toBe("Call Transcript (original and English, machine-translated)");
  });
});

describe("transcriptFilename", () => {
  const call = (patient_name: string) =>
    ({ doc_id: "CA1", patient_name, initiated_at: "2026-10-22T10:00:00Z" }) as CallDoc;
  it("folds accents instead of dropping the letter", () => {
    expect(transcriptFilename(call("José Núñez"))).toBe("transcript-jose-nunez-2026-10-22.txt");
  });
  it("keeps other scripts", () => {
    expect(transcriptFilename(call("王芳"))).toBe("transcript-王芳-2026-10-22.txt");
    expect(transcriptFilename(call("Иван Петров"))).toBe("transcript-иван-петров-2026-10-22.txt");
  });
  it("names the language view", () => {
    expect(transcriptFilename(call("Ana Ruiz"), "original")).toBe("transcript-ana-ruiz-2026-10-22-original.txt");
    expect(transcriptFilename(call("Ana Ruiz"), "english")).toBe("transcript-ana-ruiz-2026-10-22.txt");
    expect(transcriptFilename(null)).toBe("transcript-call-transcript.txt");
  });
});
