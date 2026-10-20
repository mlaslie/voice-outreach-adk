import { describe, expect, it } from "vitest";
import {
  COLOR_TOKENS,
  DEFAULT_COLORS,
  DEFAULT_TITLE,
  assetUrl,
  brandTextError,
  brandingContrast,
  brandingCss,
  brandingVars,
  colorPatch,
  parseBranding,
  resolveBranding,
  sniffImage,
  uploadError,
} from "./branding";

const BUILD = { orgName: "Build Org", logoUrl: "/brand/logo.png", logoWideUrl: "" };
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 0]);
const WEBP = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]);
const ICO = new Uint8Array([0, 0, 1, 0, 1, 0, 0, 0]);
const SVG = new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"/>');

describe("tokens", () => {
  it("expose 5–8 colours, each matching theme.css", () => {
    expect(COLOR_TOKENS.length).toBeGreaterThanOrEqual(5);
    expect(COLOR_TOKENS.length).toBeLessThanOrEqual(8);
    expect(DEFAULT_COLORS.brand).toBe("#22873b");
    for (const t of COLOR_TOKENS) expect(Object.values(t.vars)).toContain(t.default);
  });
});

describe("brandingVars / brandingCss", () => {
  it("is empty when nothing changed", () => {
    expect(brandingCss({})).toBe("");
  });

  it("overrides a token and derives its shades", () => {
    const vars = brandingVars({ brand: "#1A5FB4" });
    expect(vars["--color-brand"]).toBe("#1a5fb4");
    expect(vars["--color-brand-dark"]).toMatch(/^#[0-9a-f]{6}$/);
    expect(vars["--color-brand-dark"]).not.toBe("#1a5fb4");
    expect(vars["--primary"]).toMatch(/^[0-9.]+ [0-9.]+% [0-9.]+%$/);
    expect(vars["--color-brand-deep"]).toBeUndefined();
  });

  it("uses today's exact values for a default colour", () => {
    expect(brandingVars({ "brand-deep": "#1d4d52" })["--color-brand-deep-dark"]).toBe("#163b3f");
  });

  it("drops invalid values and unknown keys (client re-validation)", () => {
    const css = brandingCss({ brand: "red;}body{display:none", info: "#12345", evil: "#000000", urgent: "#c0392b" });
    expect(css).toBe(`:root{--color-brand-coral:#c0392b;--color-brand-coral-dark:${brandingVars({ urgent: "#c0392b" })["--color-brand-coral-dark"]}}`);
    expect(css).not.toMatch(/display|evil|12345/);
  });

  it("writes every token when complete", () => {
    const vars = brandingVars({}, true);
    expect(vars["--color-brand"]).toBe("#22873b");
    expect(vars["--color-console"]).toBe("#f4f6f5");
  });
});

describe("parseBranding / resolveBranding", () => {
  it("falls back to today's look and the build values", () => {
    const b = resolveBranding(null, BUILD);
    expect(b).toEqual({
      productTitle: DEFAULT_TITLE,
      staffOrgName: "Build Org",
      logoUrl: "/brand/logo.png",
      logoWideUrl: "",
      faviconUrl: "",
      colors: {},
    });
  });

  it("uses what the API has, re-validated", () => {
    const parsed = parseBranding({
      product_title: "Riverside Recovery Line",
      staff_org_name: "<b>bad</b>",
      colors: { brand: "#1A5FB4", info: "blue", nope: "#000000" },
      assets: { logo: { version: 3, content_type: "image/png" }, logo_wide: null, favicon: { version: "x" } },
    });
    const b = resolveBranding(parsed, BUILD);
    expect(b.productTitle).toBe("Riverside Recovery Line");
    expect(b.staffOrgName).toBe("Build Org"); // invalid name ignored
    expect(b.colors).toEqual({ brand: "#1a5fb4" });
    expect(b.logoUrl).toBe("/api/backend/branding/assets/logo?v=3");
    expect(b.logoWideUrl).toBe("");
    expect(b.faviconUrl).toBe("");
    expect(assetUrl("favicon", 7)).toBe("/api/backend/branding/assets/favicon?v=7");
  });

  it("rejects non-objects", () => {
    expect(parseBranding(null)).toBeNull();
    expect(parseBranding("x")).toBeNull();
  });
});

describe("brandTextError", () => {
  it("follows the API's rule", () => {
    expect(brandTextError("Riverside Clinic")).toBeNull();
    expect(brandTextError("É".repeat(60))).toBeNull();
    expect(brandTextError("A".repeat(61))).not.toBeNull();
    expect(brandTextError("<script>")).not.toBeNull();
    expect(brandTextError("tab\there")).not.toBeNull();
  });
});

describe("uploads", () => {
  it("sniffs the magic bytes", () => {
    expect(sniffImage(PNG)).toBe("image/png");
    expect(sniffImage(JPEG)).toBe("image/jpeg");
    expect(sniffImage(WEBP)).toBe("image/webp");
    expect(sniffImage(ICO)).toBe("image/x-icon");
    expect(sniffImage(SVG)).toBeNull();
    expect(sniffImage(new Uint8Array())).toBeNull();
  });

  it("refuses SVG, empty and oversized files", () => {
    expect(uploadError("logo", PNG)).toBeNull();
    expect(uploadError("logo", SVG)).toMatch(/SVG/);
    expect(uploadError("logo", new Uint8Array())).toMatch(/empty/);
    const big = new Uint8Array(129 * 1024);
    big.set(PNG);
    expect(uploadError("favicon", big)).toMatch(/128 KB/);
    expect(uploadError("logo", big)).toBeNull();
  });
});

describe("colorPatch", () => {
  it("sends changes, and null for a colour put back to today's value", () => {
    const draft = { ...DEFAULT_COLORS, brand: "#1a5fb4", info: DEFAULT_COLORS.info };
    expect(colorPatch(draft, { info: "#0a58ca" })).toEqual({ brand: "#1a5fb4", info: null });
    expect(colorPatch({ ...DEFAULT_COLORS }, {})).toEqual({});
    expect(colorPatch({ ...DEFAULT_COLORS, brand: "#1A5FB4" }, { brand: "#1a5fb4" })).toEqual({});
  });
});

describe("brandingContrast", () => {
  it("passes for today's palette where the theme does", () => {
    const checks = brandingContrast({ ...DEFAULT_COLORS });
    const byId = Object.fromEntries(checks.map((c) => [c.id, c]));
    expect(byId["brand-button"].pass).toBe(true);
    expect(byId["deep-sidebar"].pass).toBe(true);
    expect(byId["background-text"].pass).toBe(true);
    for (const c of checks) expect(c.ratio).toBeGreaterThanOrEqual(1);
    // Today's theme itself: the urgent badge's text (coral-dark on a 12 % coral tint) is 3.96:1.
    expect(byId["urgent-pill"].pass).toBe(false);
    expect(byId["urgent-pill"].ratio).toBeCloseTo(3.96, 1);
  });

  it("warns when white text on a chosen colour fails AA", () => {
    const checks = brandingContrast({ ...DEFAULT_COLORS, brand: "#7de69b", "brand-deep": "#dddddd" });
    const failed = checks.filter((c) => !c.pass).map((c) => c.id);
    expect(failed).toContain("brand-button");
    expect(failed).toContain("deep-sidebar");
    const button = checks.find((c) => c.id === "brand-button")!;
    expect(button.fg).toBe("#ffffff");
    expect(button.bg).toBe("#7de69b");
    expect(button.min).toBe(4.5);
  });
});

describe("bytesToBase64", () => {
  it("encodes large files in chunks", async () => {
    const { bytesToBase64 } = await import("./branding");
    expect(bytesToBase64(new Uint8Array([0x89, 0x50, 0x4e, 0x47]))).toBe("iVBORw==");
    const big = new Uint8Array(200_000).fill(7);
    expect(Buffer.from(bytesToBase64(big), "base64").equals(Buffer.from(big))).toBe(true);
  });
});
