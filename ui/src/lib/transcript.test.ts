import { afterEach, describe, expect, it } from "vitest";
import { transcriptToText } from "./transcript";
import { setTimeFormat } from "./time";
import { TranscriptTurn } from "./types";

// A local wall-clock instant, so the stamp is the same in any test zone.
const at = new Date(2026, 9, 19, 14, 30, 5).toISOString();
const turns = [{ role: "agent", text: "Hello, this is a test call.", timestamp: at }] as TranscriptTurn[];

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
