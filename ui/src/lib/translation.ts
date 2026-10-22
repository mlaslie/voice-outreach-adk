/**
 * Bilingual transcripts and texts (docs/LANGUAGES-PLAN.md §3.6, step 2).
 *
 * After a call the API translates every turn to English (`text_en`, `lang`).
 * Nurses read English by default, with a toggle to the patient's own words
 * or both side by side. An all-English call shows no toggle and no label, so
 * it looks exactly as it did before.
 *
 * Pure helpers, so the toggle and the labels can be tested.
 */

import { DEFAULT_LANGUAGE, LanguageInfo, languageName, normalizeLanguage } from "./languages";
import type { TranscriptTranslation, TranscriptTurn } from "./types";

export type TranscriptView = "english" | "original" | "side";

export const VIEW_LABELS: Record<TranscriptView, string> = {
  english: "English",
  original: "Original",
  side: "Side by side",
};

/**
 * Where the transcript stands:
 * - "translated": at least one turn has an English translation (show the toggle);
 * - "pending": not translated yet and something looks non-English ("Translating…");
 * - "failed": translation failed (show the original with a note);
 * - "none": English, or nothing to translate (show it exactly as before).
 */
export type TranslationState = "translated" | "pending" | "failed" | "none";

export function isEnglishCode(code: string | null | undefined): boolean {
  return (code ?? "").trim().toLowerCase().replace(/_/g, "-").split("-")[0] === "en";
}

// Common non-English words of the Latin-script languages on the list (the
// API's voa_core.translate.looks_english uses the same idea). Only decides
// whether to say "Translating…" while the task has not run: not a safety check.
const FOREIGN = new Set(
  `que el la los las una por para pero porque como muy tengo tiene estoy bien gracias dolor fiebre
   usted hola yo puedo cuando tambem nao sim obrigado obrigada voce eu estou muito tenho mas uma dor
   febre oui merci je suis vous nous avec mais bonjour douleur ich nicht danke bitte und ist das mir
   sehr habe schmerzen fieber grazie sono buongiorno molto dolore febbre ik niet heb het een pijn
   koorts tak jestem nie evet saya tidak terima kasih sakit demam`.split(/\s+/),
);

/** No letters outside a–z and no common word of another language. */
export function looksEnglish(text: string): boolean {
  for (const ch of text) {
    if (/\p{L}/u.test(ch) && !/[a-z]/i.test(ch)) return false;
  }
  const words = text.toLowerCase().match(/[a-z]+/g) ?? [];
  return !words.some((w) => FOREIGN.has(w));
}

export function hasTranslation(turns: { text_en?: string | null }[]): boolean {
  return turns.some((t) => !!t.text_en);
}

export function translationState(
  turns: Pick<TranscriptTurn, "text" | "text_en">[],
  translation: TranscriptTranslation | null | undefined,
): TranslationState {
  if (hasTranslation(turns)) return "translated";
  if (translation?.status === "failed") return "failed";
  if (!translation && turns.some((t) => !looksEnglish(t.text ?? ""))) return "pending";
  return "none";
}

/** The text to show for one turn in a view ("side" shows both; this is the main one). */
export function turnText(turn: Pick<TranscriptTurn, "text" | "text_en">, view: TranscriptView): string {
  if (view === "original") return turn.text;
  return turn.text_en || turn.text;
}

/** A text message's body in a view: English (when translated) or the original. */
export function messageText(m: { body: string; body_en?: string | null }, view: TranscriptView): string {
  return view === "original" ? m.body : m.body_en || m.body;
}

/**
 * The non-English language the transcript was translated from: the language
 * used when it is not English, else the first translated turn's language.
 */
export function sourceLanguage(
  turns: Pick<TranscriptTurn, "text_en" | "lang">[],
  languageUsed?: string | null,
): string | null {
  if (languageUsed && !isEnglishCode(languageUsed)) return languageUsed;
  const t = turns.find((x) => x.text_en && x.lang && !isEnglishCode(x.lang));
  return t?.lang ?? null;
}

/** "Machine-translated from Spanish (US)". */
export function translatedLabel(code: string | null | undefined, list: LanguageInfo[]): string {
  return code ? `Machine-translated from ${languageName(code, list)}` : "Machine-translated to English";
}

/** The note above the transcript, or null for an all-English call. */
export function translationNote(state: TranslationState, code: string | null, list: LanguageInfo[]): string | null {
  if (state === "translated") return translatedLabel(code, list);
  if (state === "pending") return "Translating… showing the original for now.";
  if (state === "failed") return "Translation to English failed. Showing the original words.";
  return null;
}

/**
 * The "Spoke Spanish (US)" chip: when the language the patient spoke differs
 * from the one asked for (absent = English (US)). Null otherwise, or before
 * the translation task has set it.
 */
export function spokeChip(
  requested: string | null | undefined,
  used: string | null | undefined,
  list: LanguageInfo[],
): string | null {
  if (!used) return null;
  const norm = (c: string | null | undefined) => {
    const v = c || DEFAULT_LANGUAGE;
    return normalizeLanguage(v, list) ?? (isEnglishCode(v) ? DEFAULT_LANGUAGE : v.toLowerCase());
  };
  const a = norm(requested);
  const b = norm(used);
  // Both English codes read the same way to a nurse: no chip for en-IN vs en-US.
  if (a === b || (isEnglishCode(a) && isEnglishCode(b))) return null;
  return `Spoke ${languageName(b, list)}`;
}
