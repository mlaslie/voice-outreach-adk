import { NextRequest, NextResponse } from "next/server";
import { GoogleAuth, IdTokenClient } from "google-auth-library";
import {
  FORWARD_REQUEST_HEADERS,
  FORWARD_RESPONSE_HEADERS,
  MAX_BODY_BYTES,
  WRITE_METHODS,
  actingUser,
  checkWrite,
  matchRoute,
  needsIdToken,
} from "@/lib/bff";

/**
 * The only way the browser reaches the backend.
 *
 *   browser ──same-origin──▶ /api/backend/<path> ──ID token──▶ ${API_BASE_URL}/api/<path>
 *
 * The rules (path allowlist, cross-site write refusal) live in lib/bff.ts
 * and are unit-tested there. This file is the plumbing: mint a Google-signed
 * ID token for audience API_BASE_URL (the backend's Cloud Run URL), pass the
 * IAP user on as X-Acting-User, and stream the response back unchanged —
 * status, JSON `{detail}` errors, audio ranges and the SSE feed alike.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PREFIX = "/api/backend/";

function apiBase(): string {
  return (process.env.API_BASE_URL ?? "").replace(/\/+$/, "");
}

let idTokenClient: { audience: string; client: Promise<IdTokenClient> } | null = null;

/** Cached per audience; the client refreshes the token itself before it expires. */
async function authHeader(audience: string): Promise<string | null> {
  if (!needsIdToken(audience)) return null;
  if (!idTokenClient || idTokenClient.audience !== audience) {
    idTokenClient = { audience, client: new GoogleAuth().getIdTokenClient(audience) };
  }
  try {
    const headers = await (await idTokenClient.client).getRequestHeaders();
    return headers.get("authorization");
  } catch (err) {
    idTokenClient = null; // retry from scratch next time
    throw err;
  }
}

function fail(status: number, detail: string) {
  return NextResponse.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

async function handle(req: NextRequest): Promise<Response> {
  const method = req.method.toUpperCase();

  // Use the raw, still-encoded pathname: Next's decoded params would let
  // "%2F" pass as a separator. lib/bff.ts decodes each segment itself.
  const pathname = req.nextUrl.pathname;
  if (!pathname.startsWith(PREFIX)) return fail(404, "Not found.");
  const route = matchRoute(method, pathname.slice(PREFIX.length));
  if (!route) return fail(404, "Not found.");

  let body: string | undefined;
  if (WRITE_METHODS.has(method)) {
    const declared = Number(req.headers.get("content-length") ?? "0");
    if (declared > MAX_BODY_BYTES) return fail(413, "Request body too large.");
    const text = await req.text();
    if (Buffer.byteLength(text) > MAX_BODY_BYTES) return fail(413, "Request body too large.");
    body = text.length > 0 ? text : undefined;
  }

  const verdict = checkWrite(
    method,
    {
      "sec-fetch-site": req.headers.get("sec-fetch-site"),
      origin: req.headers.get("origin"),
      "content-type": req.headers.get("content-type"),
      host: req.headers.get("host"),
      "x-forwarded-host": req.headers.get("x-forwarded-host"),
    },
    body !== undefined
  );
  if (!verdict.ok) return fail(verdict.status, verdict.detail);

  const base = apiBase();
  if (!base) return fail(503, "The dashboard is not configured: API_BASE_URL is not set on the server.");

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const v = req.headers.get(name);
    if (v) headers.set(name, v);
  }
  // Uncompressed upstream, so streams (SSE, audio) flow chunk by chunk and
  // Content-Length / Content-Range stay true.
  headers.set("accept-encoding", "identity");
  const user = actingUser(req.headers.get("x-goog-authenticated-user-email"));
  if (user) headers.set("X-Acting-User", user);

  try {
    const auth = await authHeader(base);
    if (auth) headers.set("authorization", auth);
  } catch {
    return fail(502, "Could not obtain credentials for the backend.");
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${base}/api/${route.upstreamPath}${req.nextUrl.search}`, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      // Closing the tab (or the EventSource) closes the backend stream too.
      signal: req.signal,
    });
  } catch {
    return fail(502, "Could not reach the backend.");
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const v = upstream.headers.get(name);
    if (v) out.set(name, v);
  }
  if (!out.has("cache-control")) out.set("cache-control", "no-store");
  if ((out.get("content-type") ?? "").startsWith("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }

  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const PUT = handle;
export const DELETE = handle;
