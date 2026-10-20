/**
 * Which UI this build is: the nurse console (service voa-ui) or the admin
 * console (service voa-admin). One codebase, two builds (docs/ADMIN-PLAN.md
 * section 2).
 *
 * NEXT_PUBLIC_APP_MODE is a build arg (Dockerfile ARG APP_MODE), inlined by
 * `next build` into the client bundle, the server and the middleware alike,
 * so every layer agrees on the mode. It must be written out literally for
 * Next to inline it. Unset means nurse; the Dockerfile refuses anything other
 * than nurse or admin.
 */

export type AppMode = "nurse" | "admin";

export function parseAppMode(value: string | undefined | null): AppMode {
  return value === "admin" ? "admin" : "nurse";
}

export const APP_MODE: AppMode = parseAppMode(process.env.NEXT_PUBLIC_APP_MODE);

export const isAdmin: boolean = APP_MODE === "admin";

/**
 * A console link from a runtime env var (NURSE_CONSOLE_URL on the admin
 * service, ADMIN_CONSOLE_URL on the nurse service). Read on the server and
 * passed down as a prop, never a NEXT_PUBLIC build arg, so one image can be
 * pointed at any deployment. Only an absolute http(s) URL is accepted;
 * anything else (unset, a typo, a javascript: URL) shows no link.
 */
export function consoleUrl(value: string | undefined | null): string | null {
  const v = (value ?? "").trim();
  if (!v) return null;
  try {
    const u = new URL(v);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}
