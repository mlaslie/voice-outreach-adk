/**
 * Patient languages (docs/LANGUAGES-PLAN.md, step 1).
 *
 * The list of 24 languages and which ones can be called today comes from the
 * API (GET /api/languages, voa_core/languages.py); nothing here hard-codes it.
 * A patient whose language is not available for their use case is HELD: the
 * dialler never calls them, and never in English silently, unless an admin
 * turned on "Call in English" for them.
 *
 * Pure helpers, so the console views and the CSV import can be tested.
 */

export const DEFAULT_LANGUAGE = "en-US";
const DEFAULT_CAMPAIGN = "post_op";

export interface LanguageInfo {
  code: string;
  /** English name, e.g. "Spanish (US)". */
  name: string;
  /** The language's own name, e.g. "Español". */
  native: string;
  /** Callable today for the requested use case (`campaign_id`). */
  available: boolean;
}

export interface LanguagesResponse {
  campaign_id: string;
  default: string;
  languages: LanguageInfo[];
  /** Every use case → the codes it can be called in today. */
  available_by_campaign: Record<string, string[]>;
}

/** Fields a row needs to be checked for a hold. */
export interface LanguageFields {
  language?: string | null;
  call_in_english?: boolean | null;
  campaign_id?: string | null;
}

const base = (name: string) => name.split("(")[0].trim().toLowerCase();

/**
 * The supported code for a typed value, or null. Same rules as the API's
 * `languages.normalize`: case-insensitive; `es_US` = `es-US`; a bare tag or
 * an English name ("es", "Spanish", "spanish (us)") when it names exactly one
 * code; "en" and "English" mean en-US; the native name too.
 */
export function normalizeLanguage(value: string | null | undefined, list: LanguageInfo[]): string | null {
  const key = (value ?? "").trim().replace(/_/g, "-").replace(/\s+/g, " ").toLowerCase();
  if (!key) return null;
  if (key === "en" || key === "english") return list.some((l) => l.code === DEFAULT_LANGUAGE) ? DEFAULT_LANGUAGE : null;
  const exact = list.find((l) => l.code.toLowerCase() === key);
  if (exact) return exact.code;
  const hits = new Set<string>();
  for (const l of list) {
    if (
      l.code.split("-")[0].toLowerCase() === key ||
      l.name.toLowerCase() === key ||
      base(l.name) === key ||
      l.native.toLowerCase() === key
    ) hits.add(l.code);
  }
  return hits.size === 1 ? [...hits][0] : null;
}

export function findLanguage(code: string | null | undefined, list: LanguageInfo[]): LanguageInfo | undefined {
  const c = normalizeLanguage(code, list);
  return c ? list.find((l) => l.code === c) : undefined;
}

/** "Spanish (US)"; the raw code when the list is not loaded. */
export function languageName(code: string | null | undefined, list: LanguageInfo[]): string {
  return findLanguage(code, list)?.name ?? (code || DEFAULT_LANGUAGE);
}

/** Form option text: "Spanish (US) · Español". */
export function optionLabel(l: LanguageInfo): string {
  return l.native === l.name ? l.name : `${l.name} · ${l.native}`;
}

/**
 * The language chip: "Español · Spanish (US)", or null for English (US).
 * Hidden for en-US, the default almost every patient has, so the chip marks
 * exactly the patients a nurse should notice before calling back.
 */
export function languageChip(code: string | null | undefined, list: LanguageInfo[]): string | null {
  if (!code) return null;
  const l = findLanguage(code, list);
  if (!l) return code === DEFAULT_LANGUAGE ? null : code; // not loaded yet, or unsupported: show the code
  if (l.code === DEFAULT_LANGUAGE) return null;
  return l.native === l.name ? l.name : `${l.native} · ${l.name}`;
}

/** Can this use case be called in `code` today? Unknown (list not loaded) is false. */
export function isAvailable(code: string, campaignId: string | null | undefined, resp: LanguagesResponse | null): boolean {
  if (!resp) return code === DEFAULT_LANGUAGE;
  const cid = campaignId || DEFAULT_CAMPAIGN;
  const codes = resp.available_by_campaign?.[cid];
  if (codes) return codes.includes(code);
  return resp.languages.find((l) => l.code === code)?.available ?? false;
}

/**
 * Why this patient is held, e.g. "Waiting for Spanish (US) support", or null
 * when the dialler will call them. Mirrors the API's `hold_reason`.
 */
export function holdReason(row: LanguageFields, resp: LanguagesResponse | null): string | null {
  if (row.call_in_english) return null;
  if (!row.language) return null;
  const list = resp?.languages ?? [];
  const code = normalizeLanguage(row.language, list);
  if (!code) return resp ? `Language "${row.language}" is not supported` : null;
  if (isAvailable(code, row.campaign_id, resp)) return null;
  return `Waiting for ${languageName(code, list)} support`;
}

/** The one-line explanation under the "Call in English" checkbox. */
export function callInEnglishHint(code: string, list: LanguageInfo[]): string {
  const name = languageName(code, list);
  return `Calls in ${name} aren't available yet, so this patient is held and not called. Tick to call them in English instead.`;
}

/** The language of a call (the call doc, else its patient context). */
export function callLanguage(c: { language?: string | null; patient_context?: Record<string, unknown> }): string | undefined {
  const v = c.language ?? c.patient_context?.language;
  return typeof v === "string" && v ? v : undefined;
}

/** CSV/booleans: yes/no, true/false, 1/0, y/n, blank = no. Anything else is undefined (a row error). */
export function parseYesNo(v: string | null | undefined): boolean | undefined {
  const k = (v ?? "").trim().toLowerCase();
  if (!k || ["no", "n", "false", "0"].includes(k)) return false;
  if (["yes", "y", "true", "1"].includes(k)) return true;
  return undefined;
}
