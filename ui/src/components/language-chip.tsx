"use client";

import { Languages } from "lucide-react";
import { useLanguages } from "@/components/languages-provider";
import { Pill } from "@/components/nurse/parts";
import { languageChip, languageName } from "@/lib/languages";
import { spokeChip } from "@/lib/translation";

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

/**
 * "Spoke Spanish (US)": the patient spoke a language other than the one the
 * call was placed in (set after the call by the translation task). Nothing
 * when they match, or before the call was translated.
 */
export function SpokeChip({
  requested,
  used,
  className,
}: {
  requested?: string | null;
  used?: string | null;
  className?: string;
}) {
  const langs = useLanguages();
  const text = spokeChip(requested, used, langs?.languages ?? []);
  if (!text) return null;
  return (
    <span title="The language the patient mostly spoke on this call">
      <Pill tone="teal" icon={Languages} className={className}>{text}</Pill>
    </span>
  );
}
