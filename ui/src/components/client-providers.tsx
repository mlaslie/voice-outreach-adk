"use client";

import dynamic from "next/dynamic";

// Sonner reads window.localStorage at mount; on Node 25's experimental
// global localStorage this crashes SSR. Load it client-only.
const Toaster = dynamic(
  () => import("@/components/ui/sonner").then((m) => m.Toaster),
  { ssr: false },
);

export function ClientProviders() {
  return <Toaster />;
}
