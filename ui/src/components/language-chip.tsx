"use client";

import { Languages } from "lucide-react";
import { useLanguages } from "@/components/languages-provider";
import { Pill } from "@/components/nurse/parts";
import { languageChip, languageName } from "@/lib/languages";

/**
 * The patient's language, e.g. "Español · Spanish (US)". Nothing for English
 * (US): the default for almost every patient, so the chip only appears where
 * a nurse should notice it before calling back.
 */
export function LanguageChip({ code, className }: { code?: string | null; className?: string }) {
  const langs = useLanguages();
  const text = languageChip(code, langs?.languages ?? []);
  if (!text) return null;
  return (
    <span title={`Preferred language: ${languageName(code, langs?.languages ?? [])}`}>
      <Pill tone="info" icon={Languages} className={className}>{text}</Pill>
    </span>
  );
}
