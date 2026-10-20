/**
 * Server only: the branding the root layout applies (app/layout.tsx), read
 * from `GET /api/branding` with the service's ID token, the same way the BFF
 * calls the API.
 *
 * Cached for about a minute per server instance, so a page load never waits
 * on it twice. An API that is unreachable, slow or not configured yields null
 * and the consoles keep today's look, silently. The BFF drops the cache after
 * an admin saves (invalidateBranding), so the admin's own next page load is
 * current.
 */

import { apiBase, authHeader } from "./backend-auth";
import { BrandingResponse, parseBranding } from "./branding";

const TTL_MS = 60_000;
/** A failed read is retried sooner, but not on every request. */
const FAIL_TTL_MS = 15_000;
const TIMEOUT_MS = 2_000;

interface Entry {
  at: number;
  ttl: number;
  value: Promise<BrandingResponse | null>;
}

// On globalThis: the layout and the route handler may load separate copies
// of this module, and both must see one cache.
const g = globalThis as typeof globalThis & { __voaBranding?: Entry | null };

async function fetchBranding(): Promise<BrandingResponse | null> {
  const base = apiBase();
  if (!base) return null;
  const headers: Record<string, string> = { accept: "application/json" };
  const auth = await authHeader(base);
  if (auth) headers.authorization = auth;
  const res = await fetch(`${base}/api/branding`, {
    headers,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`branding: HTTP ${res.status}`);
  return parseBranding(await res.json());
}

export function loadBranding(now = Date.now()): Promise<BrandingResponse | null> {
  const hit = g.__voaBranding;
  if (hit && now - hit.at < hit.ttl) return hit.value;
  const entry: Entry = { at: now, ttl: TTL_MS, value: Promise.resolve(null) };
  entry.value = fetchBranding().catch(() => {
    entry.ttl = FAIL_TTL_MS;
    return null;
  });
  g.__voaBranding = entry;
  return entry.value;
}

export function invalidateBranding(): void {
  g.__voaBranding = null;
}
