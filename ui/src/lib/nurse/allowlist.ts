import { AllowlistResponse } from "../types";

/**
 * Whether the proxy will let a number be dialled (F5). The proxy decides —
 * this only predicts, so the console can say "not an approved number"
 * before a nurse presses Call instead of after the dial is refused.
 *
 * Entries ending in "*" are prefixes (from OUTBOUND_ALLOWLIST).
 */
export function isAllowed(phone: string, allowlist?: AllowlistResponse | null): boolean | undefined {
  if (!allowlist) return undefined;
  if (allowlist.unrestricted) return true;
  return allowlist.entries.some((e) =>
    e.phone.endsWith("*") ? phone.startsWith(e.phone.slice(0, -1)) : e.phone === phone,
  );
}
