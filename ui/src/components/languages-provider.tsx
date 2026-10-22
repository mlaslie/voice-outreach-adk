"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LanguagesResponse } from "@/lib/languages";
import sample from "@/lib/languages-sample.json";

/**
 * The patient languages (GET /api/languages) for every component under it.
 * Live consoles read the API once per page load. Sample data (?data=demo) has
 * no backend, so it uses a copy of the API's answer checked into the repo
 * (lib/languages-sample.json; a backend test fails if it drifts).
 */
const LanguagesContext = createContext<LanguagesResponse | null>(null);

export const SAMPLE_LANGUAGES = sample as LanguagesResponse;

export function LanguagesProvider({ mode, children }: { mode: "demo" | "live"; children: React.ReactNode }) {
  const [resp, setResp] = useState<LanguagesResponse | null>(mode === "demo" ? SAMPLE_LANGUAGES : null);
  useEffect(() => {
    if (mode === "demo") return;
    let alive = true;
    api.getLanguages().then((r) => alive && setResp(r)).catch(() => {});
    return () => { alive = false; };
  }, [mode]);
  return <LanguagesContext.Provider value={resp}>{children}</LanguagesContext.Provider>;
}

/** null until loaded (or when the read failed). */
export function useLanguages(): LanguagesResponse | null {
  return useContext(LanguagesContext);
}
