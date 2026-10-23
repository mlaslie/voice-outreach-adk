/**
 * Use cases (docs/USE-CASES-PLAN.md): which kind of outreach call a patient
 * gets. The list comes from the API (GET /api/campaigns, one entry per spec
 * under config/campaigns); nothing here hard-codes it, so a new spec appears
 * in the CSV import and the patient form without a UI change.
 */

export interface CampaignInfo {
  id: string;
  name: string;
  /** The spec's review status: draft | in_review | approved | active | retired. */
  status: string;
}

export interface CampaignsResponse {
  /** The use case of an entry without one (post_op). */
  default: string;
  campaigns: CampaignInfo[];
}

/**
 * A CSV or form value as a known use-case id: the id itself in any case, with
 * "-" for "_" ("Pre-op" -> "pre_op"). Blank is null (the default applies);
 * anything not on the list is undefined, an error.
 */
export function normalizeCampaign(v: string | null | undefined, list: CampaignInfo[]): string | null | undefined {
  const k = (v ?? "").trim().toLowerCase().replace(/-/g, "_");
  if (!k) return null;
  return list.find((c) => c.id === k)?.id;
}

/** "Pre-op readiness call (draft)": the name, plus the status when not active. */
export function campaignLabel(c: CampaignInfo): string {
  return c.status === "active" ? c.name : `${c.name} (${c.status.replace("_", " ")})`;
}

/** The display name for an id (or the default for blank); the raw id if unknown. */
export function campaignName(id: string | null | undefined, resp: CampaignsResponse | null): string {
  const cid = id || resp?.default || "post_op";
  return resp?.campaigns.find((c) => c.id === cid)?.name ?? cid;
}
