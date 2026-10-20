"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { DEFAULT_BRANDING, SiteBranding, brandingCss } from "@/lib/branding";

/**
 * The branding both consoles wear. The root layout reads it on the server
 * (lib/branding-server.ts) and has already written the colour overrides and
 * the title and favicon into the page; this provider hands the logos and the
 * staff organisation name to the sidebar and brand tiles.
 *
 * `applyToTab` changes this tab only, at once: Admin → Branding uses it after
 * a save (so the admin sees the result without waiting for the server's
 * minute-long cache) and, with sample data, instead of saving at all.
 */

interface BrandingContextValue {
  branding: SiteBranding;
  applyToTab: (b: SiteBranding | null) => void;
}

const BrandingContext = createContext<BrandingContextValue>({
  branding: DEFAULT_BRANDING,
  applyToTab: () => {},
});

export function BrandingProvider({ initial, children }: { initial: SiteBranding; children: React.ReactNode }) {
  const [tab, setTab] = useState<SiteBranding | null>(null);
  const branding = tab ?? initial;

  useEffect(() => {
    if (!tab) return;
    document.title = tab.productTitle;
    setFavicon(tab.faviconUrl);
  }, [tab]);

  const value = useMemo(() => ({ branding, applyToTab: setTab }), [branding]);
  // Every token, so it fully replaces the server's overrides (brandingCss re-validates each value).
  const css = tab ? brandingCss(tab.colors, true) : "";
  return (
    <BrandingContext.Provider value={value}>
      {css ? <style id="branding-tab">{css}</style> : null}
      {children}
    </BrandingContext.Provider>
  );
}

export function useBranding(): SiteBranding {
  return useContext(BrandingContext).branding;
}

export function useApplyBranding(): (b: SiteBranding | null) => void {
  return useContext(BrandingContext).applyToTab;
}

/** Point the tab's icon at `url` (a same-origin path or a data: URL); "" removes ours. */
function setFavicon(url: string) {
  const ours = document.head.querySelector<HTMLLinkElement>('link[rel="icon"][data-branding]');
  if (!url) {
    ours?.remove();
    return;
  }
  if (!/^(\/api\/backend\/branding\/assets\/favicon\?v=\d+|data:image\/(png|jpeg|webp|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=]+)$/.test(url)) return;
  document.head.querySelectorAll('link[rel="icon"]:not([data-branding])').forEach((l) => l.remove());
  const link = ours ?? Object.assign(document.createElement("link"), { rel: "icon" });
  link.setAttribute("data-branding", "");
  link.href = url;
  if (!ours) document.head.appendChild(link);
}
