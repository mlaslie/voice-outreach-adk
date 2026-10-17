import { describe, expect, it } from "vitest";
import { CONSENT_STATEMENT, CONSENT_VERSION, consentLabel } from "./consent";
import { ORG_NAME } from "./site";

/**
 * The consent wording is registered with the carrier as part of A2P 10DLC
 * vetting and quoted verbatim in the campaign's consent description. If it
 * drifts, the registration silently stops matching the product and the next
 * review fails the same way it did the first time.
 *
 * The organisation name is the one configurable part (NEXT_PUBLIC_ORG_NAME); the
 * rest of the sentence is pinned.
 */
describe("consent statement", () => {
  it("matches the wording registered with the carrier, character for character", () => {
    expect(CONSENT_STATEMENT).toBe(
      `Optional: I agree to receive automated follow-up text messages from ${ORG_NAME} ` +
        "at the number provided. Typically one message when a scheduled follow-up " +
        "call is unanswered, plus replies if I respond. Msg & data rates may apply. " +
        "Reply STOP to opt out, HELP for help. Calls work whether or not I agree to texts."
    );
  });

  it("carries the four disclosures a reviewer checks for", () => {
    // Ownership of the number, what is being consented to, rates, and how to stop.
    expect(CONSENT_STATEMENT).toContain("text messages");
    expect(CONSENT_STATEMENT).toContain("Msg & data rates may apply");
    expect(CONSENT_STATEMENT).toContain("Reply STOP to opt out");
    expect(CONSENT_STATEMENT).toContain("HELP for help");
    // Frequency disclosure — a standard checklist item that was missing.
    expect(CONSENT_STATEMENT).toMatch(/one message when a scheduled follow-up/);
  });

  it("is versioned, so a reworded statement is distinguishable in the record", () => {
    expect(CONSENT_VERSION).toMatch(/^v\d+$/);
  });

  it("says plainly that texts are optional and calls are unaffected", () => {
    // The whole point of the rewrite: a required consent box is what the
    // carrier rejected (error 30923).
    expect(CONSENT_STATEMENT).toContain("Optional");
    expect(CONSENT_STATEMENT).toContain("Calls work whether or not");
  });

  it("never describes itself as a test", () => {
    // "for testing purposes" inside the consent instrument told the carrier
    // the opt-in was non-production. Both reviewers called it the single
    // worst string in the submission.
    expect(CONSENT_STATEMENT.toLowerCase()).not.toContain("test");
    expect(CONSENT_STATEMENT.toLowerCase()).not.toContain("my own");
  });

  it("records both answers, not only agreement", () => {
    // "Never asked" and "asked and declined" must not look identical.
    const at = new Date("2026-08-10T12:00:00Z");
    expect(consentLabel("My test handset", true, at)).toBe(
      "My test handset - sms consent v3 2026-08-10"
    );
    expect(consentLabel("My test handset", false, at)).toBe(
      "My test handset - no sms consent 2026-08-10"
    );
    expect(consentLabel("", true, at)).toBe("sms consent v3 2026-08-10");
  });

  it("never exceeds the backend's 120-character label cap", () => {
    const long = consentLabel("x".repeat(200), true, new Date("2026-08-10T12:00:00Z"));
    expect(long.length).toBeLessThanOrEqual(120);
  });
});
