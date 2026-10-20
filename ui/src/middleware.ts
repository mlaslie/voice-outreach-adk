import { NextRequest, NextResponse } from "next/server";
import { APP_MODE } from "@/lib/mode";
import { decideRoute } from "@/lib/route-mode";

/**
 * Route by build mode (lib/route-mode.ts has the rules and their tests).
 * NEXT_PUBLIC_APP_MODE is inlined at build time, so this runs with the
 * build's mode in the edge runtime.
 */
export function middleware(req: NextRequest) {
  const decision = decideRoute(APP_MODE, req.nextUrl.pathname);
  if (decision.action === "redirect") {
    const url = req.nextUrl.clone();
    url.pathname = decision.to;
    // Keep ?data=demo so a sample-data link still lands on sample data.
    const demo = req.nextUrl.searchParams.get("data") === "demo";
    url.search = demo ? "?data=demo" : "";
    return NextResponse.redirect(url);
  }
  if (decision.action === "not_found") {
    return new NextResponse("Not found", { status: 404, headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } });
  }
  return NextResponse.next();
}

export const config = {
  // Skip Next's own assets outright; everything else goes through decideRoute.
  matcher: ["/((?!_next/static|_next/image).*)"],
};
