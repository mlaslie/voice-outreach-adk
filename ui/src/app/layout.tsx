import type { Metadata } from "next";
import { connection } from "next/server";
import { Poppins, Source_Sans_3, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { DemoBanner } from "@/components/demo-banner";
import { ClientProviders } from "@/components/client-providers";
import { BrandingProvider } from "@/components/branding-provider";
import { brandingCss, resolveBranding } from "@/lib/branding";
import { loadBranding } from "@/lib/branding-server";

// Brand type, paired with the palette in app/theme.css — re-skin the two
// together. Headings are Poppins 600, monospace IBM Plex Mono, body Source
// Sans 3 (free on Google Fonts; theme.css keeps a licensed face first in the
// stack so a web-font kit can take over).
const display = Poppins({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-poppins",
});

const body = Source_Sans_3({
  subsets: ["latin"],
  variable: "--font-body",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
});

/**
 * Admin → Branding, read per request on the server (cached about a minute,
 * lib/branding-server.ts). Unreachable or unconfigured: today's look.
 */
async function siteBranding() {
  await connection(); // per request, never baked in at build time
  return resolveBranding(await loadBranding());
}

export async function generateMetadata(): Promise<Metadata> {
  const b = await siteBranding();
  return {
    title: b.productTitle,
    description: "Outbound wellness-check demo dashboard",
    ...(b.faviconUrl ? { icons: { icon: b.faviconUrl } } : {}),
  };
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const branding = await siteBranding();
  // Only #RRGGBB values from a fixed token list reach this (lib/branding.ts).
  const css = brandingCss(branding.colors);
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body>
        {css ? <style id="branding">{css}</style> : null}
        <BrandingProvider initial={branding}>
          <DemoBanner />
          {children}
        </BrandingProvider>
        <ClientProviders />
      </body>
    </html>
  );
}
