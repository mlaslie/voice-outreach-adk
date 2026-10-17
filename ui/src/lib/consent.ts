/**
 * A tester's OPTIONAL agreement to receive text messages.
 *
 * Consent to texts must never be a condition of using the tool. A2P 10DLC
 * rejects any flow that makes message consent required to create an account
 * or complete a transaction (error 30923) — a rejection this campaign
 * collected first-hand by gating the "Allow" button on it.
 *
 * So the two permissions are genuinely separate:
 *   - adding a number to the allowlist authorises CALLS, and needs no consent
 *   - this checkbox authorises TEXTS, and is entirely optional
 *
 * The separation is enforced in the follow-up service, which refuses to send
 * to any number without a consent record — not merely described here.
 *
 * The wording is registered with the carrier and quoted in the campaign's
 * consent description, so it must not drift. A test pins the exact string.
 */

import { ORG_NAME, PRIVACY_URL, TERMS_URL } from "./site";

// Re-exported so the consent UI takes everything from one module. The
// values themselves are NEXT_PUBLIC_* build args (see ./site.ts).
export { PRIVACY_URL, TERMS_URL };

export const CONSENT_STATEMENT =
  `Optional: I agree to receive automated follow-up text messages from ${ORG_NAME} ` +
  `at the number provided. Typically one message when a scheduled follow-up ` +
  `call is unanswered, plus replies if I respond. Msg & data rates may apply. ` +
  `Reply STOP to opt out, HELP for help. Calls work whether or not I agree to texts.`;

// PRIVACY_URL / TERMS_URL (above) are shown beside the checkbox. Reviewers
// look for policy links at the point of collection, not only somewhere on the
// site.

export const CONSENT_VERSION = "v3";

/** Backend caps the allowlist label at 120 characters. */
const LABEL_MAX = 120;

/**
 * Note on the allowlist entry whether texts were agreed to.
 *
 * Records both answers, not just yes. "No record" and "declined" look
 * identical otherwise, and only one of them means the tester was asked.
 */
export function consentLabel(
  label: string,
  smsConsent: boolean,
  today: Date = new Date()
): string {
  const date = today.toISOString().slice(0, 10);
  const stamp = smsConsent
    ? `sms consent ${CONSENT_VERSION} ${date}`
    : `no sms consent ${date}`;
  const combined = label ? `${label} - ${stamp}` : stamp;
  return combined.slice(0, LABEL_MAX);
}
