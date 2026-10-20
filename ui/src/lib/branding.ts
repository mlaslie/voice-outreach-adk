/**
 * Runtime branding for both staff consoles (Admin → Branding, docs/ADMIN-PLAN.md
 * section 4), as pure functions so they can be tested without a server.
 *
 * The API (`GET /api/branding`) stores only what an admin changed. Everything
 * else is today's look: the tokens in app/theme.css and the build-time
 * NEXT_PUBLIC_* values in lib/site.ts. The root layout reads branding on the
 * server (lib/branding-server.ts) and applies it as CSS variable overrides on
 * `:root`, the page title and favicon, and the sidebar's logos and name
 * (components/branding-provider.tsx).
 *
 * Patient-facing wording is NOT here: the company name patients hear and read
 * is Admin → Display (part of the SMS consent wording).
 */

import { ORG_NAME, LOGO_URL, LOGO_WIDE_URL } from "./site";
import { contrastRatio, hexToHslTriplet, isHex, meetsAA, mix, shade } from "./contrast";

export { isHex } from "./contrast";

/** The title browsers show before anyone sets one. */
export const DEFAULT_TITLE = "Post-Op Call Center — Demo";

export type ColorKey =
  | "brand"
  | "brand-deep"
  | "accent"
  | "emergency"
  | "urgent"
  | "routine"
  | "info"
  | "background";

export type AssetKind = "logo" | "logo_wide" | "favicon";
export const ASSET_KINDS: readonly AssetKind[] = ["logo", "logo_wide", "favicon"];

/** Largest upload per image (the API enforces the same). */
export const MAX_ASSET_BYTES: Record<AssetKind, number> = {
  logo: 512 * 1024,
  logo_wide: 512 * 1024,
  favicon: 128 * 1024,
};

export interface ColorToken {
  key: ColorKey;
  label: string;
  hint: string;
  /** Today's value (app/theme.css). */
  default: string;
  /**
   * The CSS variables this token sets, with today's exact values. A changed
   * colour sets the first to the colour and derives the others (hover and
   * text shades, the shadcn HSL triplets).
   */
  vars: Record<string, string>;
}

/** The colours an admin may change: the keys match the API's DEFAULT_COLORS. */
export const COLOR_TOKENS: readonly ColorToken[] = [
  {
    key: "brand",
    label: "Brand",
    hint: "Buttons, links, the focus ring and “good” badges.",
    default: "#22873b",
    vars: {
      "--color-brand": "#22873b",
      "--color-brand-dark": "#1d7232",
      "--primary": "134.9 59.76% 33.14%",
      "--ring": "134.9 59.76% 33.14%",
    },
  },
  {
    key: "brand-deep",
    label: "Brand deep",
    hint: "The sidebar, dark buttons and the active tab’s text.",
    default: "#1d4d52",
    vars: { "--color-brand-deep": "#1d4d52", "--color-brand-deep-dark": "#163b3f" },
  },
  {
    key: "accent",
    label: "Accent",
    hint: "The glow in the sidebar and panels.",
    default: "#2ea84a",
    vars: { "--color-brand-bright": "#2ea84a" },
  },
  {
    key: "emergency",
    label: "Emergency",
    hint: "Emergency badges, alerts and destructive buttons.",
    default: "#e4002b",
    vars: {
      "--color-brand-red": "#e4002b",
      "--color-brand-red-dark": "#cb0026",
      "--destructive": "348.7 100% 44.71%",
    },
  },
  {
    key: "urgent",
    label: "Urgent",
    hint: "Urgent follow-ups.",
    default: "#e5554f",
    vars: { "--color-brand-coral": "#e5554f", "--color-brand-coral-dark": "#df302a" },
  },
  {
    key: "routine",
    label: "Routine",
    hint: "Routine follow-ups and call-to-action highlights.",
    default: "#ffcd00",
    vars: { "--color-brand-yellow": "#ffcd00" },
  },
  {
    key: "info",
    label: "Info",
    hint: "Informational badges.",
    default: "#00749e",
    vars: { "--color-brand-blue": "#00749e", "--color-brand-blue-dark": "#005776" },
  },
  {
    key: "background",
    label: "Background",
    hint: "The console background behind the panels.",
    default: "#f4f6f5",
    vars: { "--color-console": "#f4f6f5" },
  },
];

export const COLOR_KEYS: readonly ColorKey[] = COLOR_TOKENS.map((t) => t.key);
export const DEFAULT_COLORS: Record<ColorKey, string> = Object.fromEntries(
  COLOR_TOKENS.map((t) => [t.key, t.default]),
) as Record<ColorKey, string>;

const TOKEN: Record<ColorKey, ColorToken> = Object.fromEntries(
  COLOR_TOKENS.map((t) => [t.key, t]),
) as Record<ColorKey, ColorToken>;

/** How much darker each derived "-dark" shade is than its colour (from theme.css). */
const DARK: Partial<Record<ColorKey, number>> = {
  brand: 0.85,
  "brand-deep": 0.76,
  emergency: 0.89,
  urgent: 0.85,
  info: 0.75,
};

/** The derived shade a changed colour uses for hover and text ("-dark" tokens). */
export function darkShade(key: ColorKey, hex: string): string {
  const v = hex.toLowerCase();
  if (v === TOKEN[key].default) {
    const dark = Object.entries(TOKEN[key].vars).find(([n]) => n.endsWith("-dark"));
    if (dark) return dark[1];
  }
  return shade(v, DARK[key] ?? 1);
}

/** The CSS variables for one token at `hex` (today's exact values when it is the default). */
export function tokenVars(key: ColorKey, hex: string): Record<string, string> {
  const t = TOKEN[key];
  const v = hex.toLowerCase();
  if (v === t.default) return { ...t.vars };
  const out: Record<string, string> = {};
  for (const name of Object.keys(t.vars)) {
    if (name.endsWith("-dark")) out[name] = darkShade(key, v);
    else if (name === "--primary" || name === "--ring" || name === "--destructive") out[name] = hexToHslTriplet(v);
    else out[name] = v;
  }
  return out;
}

/**
 * CSS variables for these colours. Invalid values and unknown keys are
 * dropped (the API already validates; this is the client's re-check).
 * `complete` also writes today's values for every colour not given, so the
 * result fully overrides an earlier override.
 */
export function brandingVars(
  colors: Partial<Record<string, string>>,
  complete = false,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of COLOR_TOKENS) {
    const v = colors[t.key];
    if (typeof v === "string" && isHex(v)) Object.assign(out, tokenVars(t.key, v));
    else if (complete) Object.assign(out, t.vars);
  }
  return out;
}

/** A `:root { … }` rule for these colours, or "" when there is nothing to override. */
export function brandingCss(colors: Partial<Record<string, string>>, complete = false): string {
  const vars = brandingVars(colors, complete);
  const body = Object.entries(vars)
    // Belt and braces: only variable names and hex / HSL values reach the style tag.
    .filter(([k, v]) => /^--[a-z-]+$/.test(k) && /^(#[0-9a-f]{6}|[0-9. %]+)$/i.test(v))
    .map(([k, v]) => `${k}:${v}`)
    .join(";");
  return body ? `:root{${body}}` : "";
}

// ---------------------------------------------------------------------------
// The API's shape
// ---------------------------------------------------------------------------

export interface BrandingAsset {
  version: number;
  content_type: string | null;
  bytes?: number | null;
}

export interface BrandingResponse {
  product_title: string | null;
  staff_org_name: string | null;
  /** Only the colours an admin changed. */
  colors: Partial<Record<ColorKey, string>>;
  default_colors?: Partial<Record<ColorKey, string>>;
  assets: Record<AssetKind, BrandingAsset | null>;
  is_default?: Record<string, unknown>;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface BrandingPatch {
  product_title?: string | null;
  staff_org_name?: string | null;
  colors?: Partial<Record<ColorKey, string | null>>;
}

/** The same rule as the API: at most 60 printable characters, no angle brackets. Null when fine. */
export function brandTextError(value: string): string | null {
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f-\u009f]/.test(value)) return "No tabs, line breaks or control characters.";
  if (/[<>]/.test(value)) return "No < or > characters.";
  if (value.trim().replace(/\s+/g, " ").length > 60) return "At most 60 characters.";
  return null;
}

function cleanText(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim().replace(/\s+/g, " ");
  return t && !brandTextError(t) ? t : null;
}

/** Re-validate a response (from the API, or anything else) into a safe BrandingResponse. */
export function parseBranding(raw: unknown): BrandingResponse | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const colors: Partial<Record<ColorKey, string>> = {};
  const rc = (r.colors && typeof r.colors === "object" ? r.colors : {}) as Record<string, unknown>;
  for (const k of COLOR_KEYS) {
    const v = rc[k];
    if (typeof v === "string" && isHex(v)) colors[k] = v.toLowerCase();
  }
  const ra = (r.assets && typeof r.assets === "object" ? r.assets : {}) as Record<string, unknown>;
  const assets = {} as Record<AssetKind, BrandingAsset | null>;
  for (const k of ASSET_KINDS) {
    const a = ra[k] as Record<string, unknown> | null | undefined;
    const version = Number(a?.version);
    assets[k] = a && Number.isSafeInteger(version) && version > 0
      ? { version, content_type: typeof a.content_type === "string" ? a.content_type : null, bytes: Number(a.bytes) || null }
      : null;
  }
  return {
    product_title: cleanText(r.product_title),
    staff_org_name: cleanText(r.staff_org_name),
    colors,
    assets,
    updated_by: typeof r.updated_by === "string" ? r.updated_by : null,
    updated_at: typeof r.updated_at === "string" ? r.updated_at : null,
  };
}

/** Same-origin URL of an uploaded image (through the BFF, behind IAP). */
export function assetUrl(kind: AssetKind, version: number): string {
  return `/api/backend/branding/assets/${kind}?v=${encodeURIComponent(String(version))}`;
}

// ---------------------------------------------------------------------------
// What the consoles show
// ---------------------------------------------------------------------------

export interface SiteBranding {
  productTitle: string;
  /** The organisation name staff see in the sidebar (not the patient-facing name). */
  staffOrgName: string;
  /** Square logo, wide logo and favicon URLs; "" when unset. */
  logoUrl: string;
  logoWideUrl: string;
  faviconUrl: string;
  /** Only the colours that differ from theme.css. */
  colors: Partial<Record<ColorKey, string>>;
}

export interface BuildDefaults {
  orgName: string;
  logoUrl: string;
  logoWideUrl: string;
}

const BUILD: BuildDefaults = { orgName: ORG_NAME, logoUrl: LOGO_URL, logoWideUrl: LOGO_WIDE_URL };

/** Branding from the API, falling back field by field to today's look and the build-time values. */
export function resolveBranding(resp: BrandingResponse | null, build: BuildDefaults = BUILD): SiteBranding {
  const a = resp?.assets;
  return {
    productTitle: resp?.product_title || DEFAULT_TITLE,
    staffOrgName: resp?.staff_org_name || build.orgName,
    logoUrl: a?.logo ? assetUrl("logo", a.logo.version) : build.logoUrl,
    logoWideUrl: a?.logo_wide ? assetUrl("logo_wide", a.logo_wide.version) : build.logoWideUrl,
    faviconUrl: a?.favicon ? assetUrl("favicon", a.favicon.version) : "",
    colors: { ...(resp?.colors ?? {}) },
  };
}

/** Today's look: what Reset to defaults returns to. */
export const DEFAULT_BRANDING: SiteBranding = resolveBranding(null);

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------

export type ImageType = "image/png" | "image/jpeg" | "image/webp" | "image/x-icon";

/** The image type from its first bytes (PNG, JPEG, WebP, ICO), or null; SVG is never accepted. */
export function sniffImage(b: Uint8Array): ImageType | null {
  const at = (i: number, ...xs: number[]) => xs.every((x, j) => b[i + j] === x);
  if (b.length >= 8 && at(0, 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)) return "image/png";
  if (b.length >= 3 && at(0, 0xff, 0xd8, 0xff)) return "image/jpeg";
  if (b.length >= 12 && at(0, 0x52, 0x49, 0x46, 0x46) && at(8, 0x57, 0x45, 0x42, 0x50)) return "image/webp";
  if (b.length >= 6 && at(0, 0, 0, 1, 0) && !(b[4] === 0 && b[5] === 0)) return "image/x-icon";
  return null;
}

/** Why this file cannot be uploaded as `kind`, or null when it can. */
export function uploadError(kind: AssetKind, bytes: Uint8Array): string | null {
  if (!bytes.length) return "The file is empty.";
  if (bytes.length > MAX_ASSET_BYTES[kind]) return `The file is larger than ${MAX_ASSET_BYTES[kind] / 1024} KB.`;
  if (!sniffImage(bytes)) return "Use a PNG, JPEG, WebP or ICO image. SVG is not accepted (it can carry scripts).";
  return null;
}

/** The colours to send: changed ones as hex, ones back at today's value as null (reset). */
export function colorPatch(
  draft: Record<ColorKey, string>,
  saved: Partial<Record<ColorKey, string>>,
): Partial<Record<ColorKey, string | null>> {
  const out: Partial<Record<ColorKey, string | null>> = {};
  for (const t of COLOR_TOKENS) {
    const v = draft[t.key].toLowerCase();
    const isDefault = v === t.default;
    const was = saved[t.key];
    if (isDefault && was) out[t.key] = null;
    else if (!isDefault && v !== was) out[t.key] = v;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Contrast (WCAG AA)
// ---------------------------------------------------------------------------

export const INK = "#25282a";
export const WHITE = "#ffffff";

export interface ContrastCheck {
  id: string;
  /** Where this pair shows, e.g. "Button text: white on Brand". */
  label: string;
  fg: string;
  bg: string;
  ratio: number;
  /** 4.5 for normal text, 3 for large. */
  min: number;
  pass: boolean;
}

/**
 * Every text-on-colour pair the consoles draw from these tokens, checked
 * against WCAG AA. Tints are computed as the console draws them (a token at
 * 10–25 % over white), and "-dark" text shades as tokenVars derives them.
 */
export function brandingContrast(colors: Record<ColorKey, string>): ContrastCheck[] {
  const c = { ...DEFAULT_COLORS } as Record<ColorKey, string>;
  for (const k of COLOR_KEYS) if (isHex(colors[k])) c[k] = colors[k].toLowerCase();
  const pairs: [string, string, string, string, boolean?][] = [
    ["brand-button", "Button text: white on Brand", WHITE, c.brand],
    ["brand-link", "Links: Brand on white", c.brand, WHITE],
    ["deep-sidebar", "Sidebar text: white on Brand deep", WHITE, c["brand-deep"]],
    ["deep-active", "Active tab: Brand deep on white", c["brand-deep"], WHITE],
    ["emergency-button", "Alert button: white on Emergency", WHITE, c.emergency],
    ["emergency-pill", "Emergency badge: dark Emergency on its tint", darkShade("emergency", c.emergency), mix(c.emergency, WHITE, 0.1)],
    ["urgent-pill", "Urgent badge: dark Urgent on its tint", darkShade("urgent", c.urgent), mix(c.urgent, WHITE, 0.12)],
    ["routine-pill", "Routine badge: text on Routine tint", INK, mix(c.routine, WHITE, 0.25)],
    ["info-pill", "Info badge: dark Info on its tint", darkShade("info", c.info), mix(c.info, WHITE, 0.1)],
    ["background-text", "Page text on Background", INK, c.background],
  ];
  return pairs.map(([id, label, fg, bg, large]) => {
    const ratio = contrastRatio(fg, bg);
    const min = large ? 3 : 4.5;
    return { id, label, fg, bg, ratio, min, pass: meetsAA(ratio, large) };
  });
}

/** Base64 of these bytes (chunked, so a 512 KB image does not overflow the call stack). */
export function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}
