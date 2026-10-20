import { AppMode } from "./mode";

/**
 * Which pages each build serves, as a pure function so it can be tested
 * without Next (src/middleware.ts applies it).
 *
 *   admin build:  /  -> /admin;  /nurse and /classic -> /admin
 *   nurse build:  /admin -> 404;  /classic -> /nurse
 *   (/classic was the retired classic dashboard; old bookmarks land on the
 *   build's console.)
 *   both:         /api/backend/*, /health, /_next/* and static files pass
 *
 * The BFF (lib/bff.ts) separately refuses the other build's API routes, so
 * a page reached some other way still cannot act outside its role.
 */
export type RouteDecision =
  | { action: "next" }
  | { action: "redirect"; to: string }
  | { action: "not_found" };

const NEXT: RouteDecision = { action: "next" };

/** `pathname` is `prefix` itself or below it ("/admin", "/admin/x"; not "/administrator"). */
function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Always served, whatever the mode. */
export function isInfrastructure(pathname: string): boolean {
  if (under(pathname, "/api/backend") || under(pathname, "/_next") || pathname === "/health") return true;
  // Static files from public/ (favicon.ico, /brand/logo.svg, robots.txt …):
  // the last segment has an extension. Page routes here never do.
  const last = pathname.slice(pathname.lastIndexOf("/") + 1);
  return /\.[A-Za-z0-9]{1,8}$/.test(last);
}

export function decideRoute(mode: AppMode, pathname: string): RouteDecision {
  if (isInfrastructure(pathname)) return NEXT;
  if (mode === "admin") {
    if (pathname === "/" || pathname === "") return { action: "redirect", to: "/admin" };
    if (under(pathname, "/nurse") || under(pathname, "/classic")) return { action: "redirect", to: "/admin" };
    return NEXT;
  }
  if (under(pathname, "/admin")) return { action: "not_found" };
  if (under(pathname, "/classic")) return { action: "redirect", to: "/nurse" };
  return NEXT;
}
