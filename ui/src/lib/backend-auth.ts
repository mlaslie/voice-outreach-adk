/**
 * Server only: the backend's base URL and a Google-signed ID token for it.
 * Shared by the BFF route handler (app/api/backend/[...path]/route.ts) and
 * the root layout's branding read (lib/branding-server.ts).
 */

import { GoogleAuth, IdTokenClient } from "google-auth-library";
import { needsIdToken } from "./bff";

export function apiBase(): string {
  return (process.env.API_BASE_URL ?? "").replace(/\/+$/, "");
}

let idTokenClient: { audience: string; client: Promise<IdTokenClient> } | null = null;

/** Cached per audience; the client refreshes the token itself before it expires. Null for a local backend. */
export async function authHeader(audience: string): Promise<string | null> {
  if (!needsIdToken(audience)) return null;
  if (!idTokenClient || idTokenClient.audience !== audience) {
    idTokenClient = { audience, client: new GoogleAuth().getIdTokenClient(audience) };
  }
  try {
    const headers = await (await idTokenClient.client).getRequestHeaders();
    return headers.get("authorization");
  } catch (err) {
    idTokenClient = null; // retry from scratch next time
    throw err;
  }
}
