import { NextRequest, NextResponse } from "next/server";
import { GoogleAuth, IdTokenClient } from "google-auth-library";
import { actingUser, needsIdToken } from "@/lib/bff";
import { isAdmin } from "@/lib/mode";

/**
 * The nurse Analytics tab's one read:
 *
 *   browser ──▶ /api/analytics/summary?days&channel ──ID token──▶ ${ANALYTICS_URL}/analytics/summary
 *
 * Nurse build only. The analytics service is private and accepts only this
 * service's identity (docs/ANALYTICS-PLAN.md). Only `days` and `channel` are
 * passed on, validated here.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CHANNELS = new Set(["all", "voice", "sms"]);
let client: { audience: string; client: Promise<IdTokenClient> } | null = null;

function fail(status: number, detail: string) {
  return NextResponse.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

export async function GET(req: NextRequest): Promise<Response> {
  if (isAdmin) return fail(404, "Not found.");
  const base = (process.env.ANALYTICS_URL ?? "").replace(/\/+$/, "");
  if (!base) return fail(503, "Analytics is not set up for this deployment.");

  const days = Number(req.nextUrl.searchParams.get("days") ?? "30");
  const channel = req.nextUrl.searchParams.get("channel") ?? "all";
  if (!Number.isInteger(days) || days < 1 || days > 365) return fail(400, "days must be 1–365.");
  if (!CHANNELS.has(channel)) return fail(400, "channel must be all, voice or sms.");

  const headers = new Headers({ accept: "application/json" });
  const user = actingUser(req.headers.get("x-goog-authenticated-user-email"));
  if (user) headers.set("X-Acting-User", user);
  if (needsIdToken(base)) {
    try {
      if (!client || client.audience !== base) {
        client = { audience: base, client: new GoogleAuth().getIdTokenClient(base) };
      }
      const auth = (await (await client.client).getRequestHeaders()).get("authorization");
      if (auth) headers.set("authorization", auth);
    } catch {
      client = null;
      return fail(502, "Could not obtain credentials for analytics.");
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${base}/analytics/summary?days=${days}&channel=${channel}`, {
      headers,
      cache: "no-store",
      redirect: "manual",
      signal: req.signal,
    });
  } catch {
    return fail(502, "Could not reach analytics.");
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
  });
}
