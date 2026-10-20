/**
 * Colour maths for Admin → Branding: WCAG 2.x contrast and the shades the
 * theme derives from a chosen colour. Pure functions, tested in
 * contrast.test.ts.
 */

export const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export function isHex(v: unknown): v is string {
  return typeof v === "string" && HEX_RE.test(v);
}

export function hexToRgb(hex: string): [number, number, number] {
  if (!isHex(hex)) throw new Error(`not #RRGGBB: ${hex}`);
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex([r, g, b]: [number, number, number]): string {
  const c = (x: number) => Math.round(Math.min(255, Math.max(0, x))).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** WCAG relative luminance, 0 (black) to 1 (white). */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio, 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** AA: 4.5:1 for normal text, 3:1 for large text (18 pt, or 14 pt bold). */
export function meetsAA(ratio: number, large = false): boolean {
  return ratio >= (large ? 3 : 4.5);
}

/** `hex` scaled towards black (factor < 1). */
export function shade(hex: string, factor: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex([r * factor, g * factor, b * factor]);
}

/** `amount` (0–1) of `fg` over `bg`: what a `bg-x/12` tint looks like on white. */
export function mix(fg: string, bg: string, amount: number): string {
  const a = hexToRgb(fg);
  const b = hexToRgb(bg);
  return rgbToHex([0, 1, 2].map((i) => a[i] * amount + b[i] * (1 - amount)) as [number, number, number]);
}

/** "H S% L%", the shadcn variables' format in theme.css. */
export function hexToHslTriplet(hex: string): string {
  const [r, g, b] = hexToRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  const f = (x: number) => String(Math.round(x * 100) / 100);
  return `${f(h)} ${f(s * 100)}% ${f(l * 100)}%`;
}
