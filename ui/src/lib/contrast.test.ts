import { describe, expect, it } from "vitest";
import { contrastRatio, hexToHslTriplet, hexToRgb, isHex, meetsAA, mix, relativeLuminance, shade } from "./contrast";

describe("WCAG contrast", () => {
  it("matches the reference values", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    // #767676 on white is the classic 4.54:1 grey.
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
    expect(contrastRatio("#ffffff", "#767676")).toBeCloseTo(4.54, 2); // order does not matter
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
    expect(relativeLuminance("#000000")).toBe(0);
  });

  it("applies the AA thresholds", () => {
    expect(meetsAA(4.5)).toBe(true);
    expect(meetsAA(4.49)).toBe(false);
    expect(meetsAA(3, true)).toBe(true);
    expect(meetsAA(2.99, true)).toBe(false);
  });
});

describe("colour helpers", () => {
  it("validates #RRGGBB only", () => {
    expect(isHex("#22873b")).toBe(true);
    expect(isHex("#22873B")).toBe(true);
    for (const bad of ["22873b", "#2287", "#22873bb", "#gggggg", "red", "", null, 5]) expect(isHex(bad)).toBe(false);
    expect(() => hexToRgb("red")).toThrow();
  });

  it("shades, mixes and converts", () => {
    expect(hexToRgb("#22873b")).toEqual([0x22, 0x87, 0x3b]);
    expect(shade("#ffffff", 0.5)).toBe("#808080");
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mix("#e5554f", "#ffffff", 0)).toBe("#ffffff");
    // The theme's own triplet for #22873b, within rounding.
    const [h, s, l] = hexToHslTriplet("#22873b").split(" ").map(parseFloat);
    expect(h).toBeCloseTo(134.9, 0);
    expect(s).toBeCloseTo(59.76, 0);
    expect(l).toBeCloseTo(33.14, 0);
    expect(hexToHslTriplet("#ffffff")).toBe("0 0% 100%");
  });
});
