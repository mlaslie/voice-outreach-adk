/**
 * Backend-for-frontend rules, as pure functions so they can be tested
 * without a server.
 *
 * The browser never talks to the backend. Every request goes to this app's
 * own same-origin route, src/app/api/backend/[...path]/route.ts, which:
 *
 *   1. checks the method + path against ALLOWED_ROUTES (`matchRoute`),
 *      including whether this build (nurse or admin) may use the route;
 *   2. for writes, refuses anything a cross-site page could have sent
 *      (`checkWrite`) — review finding U-2;
 *   3. forwards to `${API_BASE_URL}/api/<path>` with a Google-signed ID token
 *      and the IAP user as `X-Acting-User`.
 *
 * The allowlist is the whole backend surface the dashboard needs. A blanket
 * pass-through would let anyone who can load the dashboard reach every
 * backend endpoint (including ones that dial people on a timer) with the
 * dashboard's own service identity.
 */

import { APP_MODE, AppMode } from "./mode";

export type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export const WRITE_METHODS: ReadonlySet<string> = new Set(["POST", "PATCH", "PUT", "DELETE"]);

/** Largest request body forwarded (a bulk CSV import is the biggest). */
export const MAX_BODY_BYTES = 2 * 1024 * 1024;

// Segment shapes. Each path segment is decoded before matching, so these
// are checked against the real value, never a percent-encoded disguise.
const ID = "[A-Za-z0-9_-]{1,64}";
const PHONE = "\\+[0-9]{6,15}";
/** A branding image (lib/branding.ts ASSET_KINDS). */
const ASSET = "(logo|logo_wide|favicon)";

/**
 * Which build may use a route (docs/ADMIN-PLAN.md section 2):
 *   - "nurse": clinical work, only the nurse console (service voa-ui);
 *   - "admin": configuration, only the admin console (service voa-admin);
 *   - "both":  reads, which both consoles show, and cancelling a booked
 *              text callback (nurses and admins both may).
 * Two reads are admin only: a call's millisecond event timeline (Call
 * diagnostics) and the configuration audit log.
 * The API enforces the same split by caller identity and refuses an admin
 * route from the nurse UI's service account. Enforcing it here as well means
 * neither build can even try the other's writes.
 */
export type RouteScope = "nurse" | "admin" | "both";

/** Every backend route the UI uses: [method, path after /api/, scope]. */
export const ALLOWED_ROUTES: ReadonlyArray<readonly [Method, RegExp, RouteScope]> = [
  // Calls
  ["GET", /^calls$/, "both"],
  ["GET", /^calls\/stream$/, "both"],
  ["POST", /^calls\/batch-delete$/, "nurse"],
  ["GET", new RegExp(`^calls/${ID}$`), "both"],
  ["GET", new RegExp(`^calls/${ID}/transcript$`), "both"],
  ["GET", new RegExp(`^calls/${ID}/recording$`), "both"],
  ["GET", new RegExp(`^calls/${ID}/events$`), "admin"],
  ["POST", new RegExp(`^calls/${ID}/follow-up$`), "nurse"],
  ["GET", /^follow-ups\/open$/, "both"],
  ["POST", /^place-call$/, "nurse"],
  ["POST", /^test-call$/, "admin"],

  // Call list: patient records are configuration; running, cancelling and
  // rescheduling calls is the nurses' work.
  ["GET", /^call-list$/, "both"],
  ["POST", /^call-list$/, "admin"],
  ["POST", /^call-list\/bulk$/, "admin"],
  ["POST", /^call-list\/reset$/, "admin"],
  ["POST", /^call-list\/cancel$/, "nurse"],
  ["GET", /^call-list\/run$/, "both"],
  ["POST", /^call-list\/run$/, "nurse"],
  ["POST", /^call-list\/run\/cancel$/, "nurse"],
  ["PATCH", new RegExp(`^call-list/${ID}$`), "admin"],
  ["DELETE", new RegExp(`^call-list/${ID}$`), "admin"],
  ["POST", new RegExp(`^call-list/${ID}/reschedule$`), "nurse"],

  // Scheduled call runs
  ["GET", /^call-runs$/, "both"],
  ["POST", /^call-runs$/, "admin"],
  ["PATCH", new RegExp(`^call-runs/${ID}$`), "admin"],
  ["DELETE", new RegExp(`^call-runs/${ID}$`), "admin"],

  // Settings
  ["GET", /^call-settings$/, "both"],
  ["PUT", /^call-settings\/recall$/, "admin"],
  ["PUT", /^call-settings\/display$/, "admin"],
  ["PUT", /^call-settings\/organization$/, "admin"],

  // Outbound allowlist
  ["GET", /^allowlist$/, "both"],
  ["POST", /^allowlist$/, "admin"],
  ["DELETE", new RegExp(`^allowlist/${PHONE}$`), "admin"],

  // SMS follow-ups
  ["GET", /^sms\/sessions$/, "both"],
  ["GET", /^sms\/callbacks$/, "both"],
  ["DELETE", new RegExp(`^sms/callbacks/${ID}$`), "both"],
  ["GET", /^sms\/messages$/, "both"],
  ["GET", /^sms\/consent$/, "both"],
  ["POST", /^sms\/consent$/, "admin"],
  ["GET", /^sms\/settings$/, "both"],
  ["POST", /^sms\/settings$/, "admin"],
  // Shared SMS number switch (docs/sms-number-switch.md)
  ["GET", /^sms\/control$/, "both"],
  ["POST", /^sms\/control$/, "admin"],
  // Dry-run simulator (the backend refuses unless SMS is in dry-run mode)
  ["POST", /^sms\/test\/start$/, "admin"],
  ["POST", /^sms\/test\/reply$/, "admin"],

  // Configuration audit log (newest first)
  ["GET", /^audit$/, "admin"],

  // Staff-console branding (Admin → Branding). Both consoles read it (the root
  // layout applies it on the server; the favicon and logos are images served
  // through here); only the admin console changes it.
  ["GET", /^branding$/, "both"],
  ["PUT", /^branding$/, "admin"],
  ["DELETE", /^branding$/, "admin"],
  ["GET", new RegExp(`^branding/assets/${ASSET}$`), "both"],
  ["PUT", new RegExp(`^branding/assets/${ASSET}$`), "admin"],
];

/** Whether a route of this scope is served by a build in this mode. */
export function scopeAllows(scope: RouteScope, mode: AppMode): boolean {
  return scope === "both" || scope === mode;
}

/** Characters allowed in any decoded path segment. Excludes "/", "\", "%", "?", "#". */
const SAFE_SEGMENT = /^[A-Za-z0-9_.+-]{1,128}$/;

export interface RouteMatch {
  /** Decoded segments, e.g. ["calls", "CA…", "transcript"]. */
  segments: string[];
  /** Re-encoded path to append to `${API_BASE_URL}/api/`. */
  upstreamPath: string;
}

/**
 * Split the raw (still percent-encoded) path after `/api/backend/`, decode
 * each segment, reject anything that could traverse or smuggle a separator,
 * and check the result against ALLOWED_ROUTES for this build's mode (the
 * other build's routes are refused). Returns null when not allowed.
 */
export function matchRoute(method: string, rawPath: string, mode: AppMode = APP_MODE): RouteMatch | null {
  const m = method.toUpperCase();
  if (!rawPath || rawPath.startsWith("/") || rawPath.endsWith("/")) return null;
  const segments: string[] = [];
  for (const raw of rawPath.split("/")) {
    let seg: string;
    try {
      seg = decodeURIComponent(raw);
    } catch {
      return null;
    }
    if (!SAFE_SEGMENT.test(seg) || seg === "." || seg === "..") return null;
    segments.push(seg);
  }
  const joined = segments.join("/");
  if (!ALLOWED_ROUTES.some(([am, re, scope]) => am === m && re.test(joined) && scopeAllows(scope, mode))) return null;
  return { segments, upstreamPath: segments.map(encodeURIComponent).join("/") };
}

/** The headers `checkWrite` looks at, lower-cased names. */
export interface WriteHeaders {
  "sec-fetch-site"?: string | null;
  origin?: string | null;
  "content-type"?: string | null;
  host?: string | null;
  "x-forwarded-host"?: string | null;
}

export type WriteCheck = { ok: true } | { ok: false; status: number; detail: string };

/** Host (with port) the browser used to reach this app. */
function requestHost(h: WriteHeaders): string | null {
  const fwd = h["x-forwarded-host"]?.split(",")[0]?.trim();
  return (fwd || h.host || "").toLowerCase() || null;
}

function isJson(contentType: string | null | undefined): boolean {
  if (!contentType) return false;
  return contentType.split(";")[0].trim().toLowerCase() === "application/json";
}

/**
 * Cross-site write protection for POST/PATCH/PUT/DELETE.
 *
 * - `Sec-Fetch-Site` is set by the browser and cannot be forged by a page.
 *   When present it must be `same-origin` (not `same-site`: a sibling
 *   subdomain is a different app).
 * - Without it (older browsers), `Origin` must be present and name this
 *   host. A write with neither header is refused: every browser that can run
 *   this dashboard sends at least one of them on a fetch() write.
 * - A request with a body must say `Content-Type: application/json`. An HTML
 *   form can only send form or text bodies, so this also closes the
 *   "simple request" path that skips CORS preflight.
 *
 * `hasBody` is whether the request carries a non-empty body.
 */
export function checkWrite(method: string, headers: WriteHeaders, hasBody: boolean): WriteCheck {
  if (!WRITE_METHODS.has(method.toUpperCase())) return { ok: true };

  const site = headers["sec-fetch-site"]?.trim().toLowerCase();
  if (site) {
    if (site !== "same-origin") {
      return { ok: false, status: 403, detail: "Cross-site request refused." };
    }
  } else {
    const origin = headers.origin?.trim();
    if (!origin || origin === "null") {
      return { ok: false, status: 403, detail: "Missing Origin; cross-site request refused." };
    }
    let originHost: string;
    try {
      originHost = new URL(origin).host.toLowerCase();
    } catch {
      return { ok: false, status: 403, detail: "Malformed Origin; request refused." };
    }
    const host = requestHost(headers);
    if (!host || originHost !== host) {
      return { ok: false, status: 403, detail: "Cross-origin request refused." };
    }
  }

  if (hasBody && !isJson(headers["content-type"])) {
    return { ok: false, status: 415, detail: "Request body must be application/json." };
  }
  return { ok: true };
}

/** IAP sends `accounts.google.com:user@example.org`; the backend wants the address. */
export function actingUser(iapHeader: string | null | undefined): string | null {
  if (!iapHeader) return null;
  const v = iapHeader.replace(/^accounts\.google\.com:/, "").trim();
  return v || null;
}

/** No ID token for a backend on this machine (local development). */
export function needsIdToken(apiBaseUrl: string): boolean {
  return !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i.test(apiBaseUrl);
}

/** Request headers copied from the browser to the backend (everything else is dropped). */
export const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "range", "last-event-id", "if-none-match"] as const;

/** Response headers copied from the backend to the browser. */
export const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "cache-control",
  "etag",
  "content-disposition",
  "x-content-type-options",
] as const;
