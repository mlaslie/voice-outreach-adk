import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateBranding, loadBranding } from "./branding-server";

const BODY = {
  product_title: "Riverside Recovery Line",
  staff_org_name: null,
  colors: { brand: "#1a5fb4", info: "javascript:x" },
  assets: { logo: null, logo_wide: null, favicon: { version: 4, content_type: "image/x-icon" } },
};

describe("loadBranding (the root layout's read)", () => {
  const saved = process.env.API_BASE_URL;
  beforeEach(() => {
    invalidateBranding();
    process.env.API_BASE_URL = "http://localhost:8080"; // local: no ID token needed
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    process.env.API_BASE_URL = saved;
    invalidateBranding();
  });

  it("reads GET /api/branding, re-validates it and caches it for a minute", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(BODY), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const b = await loadBranding(1_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe("http://localhost:8080/api/branding");
    expect(b?.product_title).toBe("Riverside Recovery Line");
    expect(b?.colors).toEqual({ brand: "#1a5fb4" });
    expect(b?.assets.favicon?.version).toBe(4);
    await loadBranding(30_000);
    expect(fetchMock).toHaveBeenCalledTimes(1); // cached
    await loadBranding(62_000);
    expect(fetchMock).toHaveBeenCalledTimes(2); // expired
  });

  it("falls back silently to null when the API fails or is not configured", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 503 })));
    expect(await loadBranding(1_000)).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    invalidateBranding();
    expect(await loadBranding(1_000)).toBeNull();
    invalidateBranding();
    process.env.API_BASE_URL = "";
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    expect(await loadBranding(1_000)).toBeNull();
    expect(f).not.toHaveBeenCalled();
  });

  it("is dropped after an admin saves", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(BODY), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await loadBranding(1_000);
    invalidateBranding();
    await loadBranding(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
