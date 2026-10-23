"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { CampaignsResponse } from "@/lib/campaigns";
import sample from "@/lib/campaigns-sample.json";

/**
 * The use cases (GET /api/campaigns) for every component under it. Live
 * consoles read the API once per page load. Sample data (?data=demo) has no
 * backend, so it uses a copy of the API's answer checked into the repo
 * (lib/campaigns-sample.json; a backend test fails if it drifts).
 */
const CampaignsContext = createContext<CampaignsResponse | null>(null);

export const SAMPLE_CAMPAIGNS = sample as CampaignsResponse;

export function CampaignsProvider({ mode, children }: { mode: "demo" | "live"; children: React.ReactNode }) {
  const [resp, setResp] = useState<CampaignsResponse | null>(mode === "demo" ? SAMPLE_CAMPAIGNS : null);
  useEffect(() => {
    if (mode === "demo") return;
    let alive = true;
    api.getCampaigns().then((r) => alive && setResp(r)).catch(() => {});
    return () => { alive = false; };
  }, [mode]);
  return <CampaignsContext.Provider value={resp}>{children}</CampaignsContext.Provider>;
}

/** null until loaded (or when the read failed). */
export function useCampaigns(): CampaignsResponse | null {
  return useContext(CampaignsContext);
}
