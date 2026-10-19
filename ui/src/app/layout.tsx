import type { Metadata } from "next";
import { Poppins, Source_Sans_3, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { DemoBanner } from "@/components/demo-banner";
import { ClientProviders } from "@/components/client-providers";

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

export const metadata: Metadata = {
  title: "Post-Op Call Center — Demo",
  description: "Outbound wellness-check demo dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body>
        <DemoBanner />
        {children}
        <ClientProviders />
      </body>
    </html>
  );
}
