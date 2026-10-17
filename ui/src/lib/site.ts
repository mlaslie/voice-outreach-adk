/**
 * Institution-specific values shown in the dashboard, in one place.
 *
 * They are NEXT_PUBLIC_* build args (Dockerfile / cloudbuild.yaml); for
 * `npm run dev`, put them in .env.local (see .env.local.example). They are
 * compiled into the client bundle, so a change needs a rebuild, not a
 * restart. The Docker build fails if a required one is empty.
 *
 * Each `process.env.NEXT_PUBLIC_X` must be written out literally — Next only
 * inlines direct references. The fallbacks are obvious placeholders so an
 * unconfigured local run is visibly unconfigured rather than wearing someone
 * else's name.
 *
 * None of these is a secret, and none is a backend URL: the browser only
 * ever talks to this app's own /api/backend/* routes (see lib/bff.ts).
 */

export const ORG_NAME = process.env.NEXT_PUBLIC_ORG_NAME || "Your Organization";
export const SUPPORT_EMAIL = process.env.NEXT_PUBLIC_SUPPORT_EMAIL || "support@example.org";
export const PRIVACY_URL = process.env.NEXT_PUBLIC_PRIVACY_URL || "https://example.org/privacy";
export const TERMS_URL = process.env.NEXT_PUBLIC_TERMS_URL || "https://example.org/terms";

/**
 * Optional logo for the nurse console (/nurse) — a path under public/ such
 * as "/brand/logo.svg", or a full URL. Unset shows a neutral placeholder mark.
 * Use an organisation's real logo only with its permission, and never commit
 * it (public/brand/ is gitignored).
 */
export const LOGO_URL = process.env.NEXT_PUBLIC_LOGO_URL || "";

/**
 * Optional wide logo (mark + wordmark) for the nurse console's sidebar,
 * shown on a white card. Unset falls back to LOGO_URL beside ORG_NAME.
 */
export const LOGO_WIDE_URL = process.env.NEXT_PUBLIC_LOGO_WIDE_URL || "";
