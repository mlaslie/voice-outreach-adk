"use client";

import { Languages } from "lucide-react";
import { useLanguages } from "@/components/languages-provider";
import { Pill } from "@/components/nurse/parts";
import { WordingSource, languageChip, languageName, spokeAutoApprovedLabel } from "@/lib/languages";
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
 *
 * A call whose wording was unreviewed (demo auto-approve: a draft pack or
 * machine-translated) shows "Spoke Vietnamese (auto-approved, unreviewed
 * wording)" instead, in warning colours, so it is never mistaken for an
 * approved-language call.
 */
export function SpokeChip({
  requested,
  used,
  source,
  className,
}: {
  requested?: string | null;
  used?: string | null;
  source?: WordingSource | null;
  className?: string;
}) {
  const langs = useLanguages();
  const list = langs?.languages ?? [];
  const auto = spokeAutoApprovedLabel({ language: requested, language_used: used, language_source: source }, list);
  if (auto) {
    return (
      <span title="Demo: this call used wording no translator has reviewed (all languages auto-approved). Not for real patients.">
        {/* Solid amber whatever the surface (the drawer header is dark): it must stand out. */}
        <Pill tone="warn" icon={Languages} className="bg-brand-orange text-ink ring-brand-orange">
          {auto}
        </Pill>
      </span>
    );
  }
  const text = spokeChip(requested, used, list);
  if (!text) return null;
  return (
    <span title="The language the patient mostly spoke on this call">
      <Pill tone="teal" icon={Languages} className={className}>{text}</Pill>
    </span>
  );
}
