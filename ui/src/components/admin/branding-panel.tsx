"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CheckCircle2, ImageUp, Palette, RotateCcw, Type } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import {
  ASSET_KINDS,
  AssetKind,
  BrandingResponse,
  COLOR_TOKENS,
  ColorKey,
  DEFAULT_BRANDING,
  DEFAULT_COLORS,
  DEFAULT_TITLE,
  MAX_ASSET_BYTES,
  SiteBranding,
  brandTextError,
  brandingContrast,
  brandingVars,
  bytesToBase64,
  colorPatch,
  isHex,
  parseBranding,
  resolveBranding,
  sniffImage,
  uploadError,
} from "@/lib/branding";
import { ORG_NAME } from "@/lib/site";
import { cn } from "@/lib/utils";
import { Panel, Pill } from "@/components/nurse/parts";
import { useApplyBranding, useBranding } from "@/components/branding-provider";

/**
 * Admin → Branding (docs/ADMIN-PLAN.md section 4): the staff consoles' title,
 * organisation name, logos, favicon and colours, with a live preview and a
 * WCAG AA contrast check. Live: saved through the API (audited), seen by both
 * consoles within a minute. Sample data: applied to this tab only, nothing sent.
 */

interface Upload {
  contentType: string;
  base64: string;
}

interface Draft {
  title: string;
  orgName: string;
  colors: Record<ColorKey, string>;
  /** Preview URL of each image: the saved one, a build-time default, or a new upload's data: URL. */
  images: Record<AssetKind, string>;
  uploads: Partial<Record<AssetKind, Upload>>;
}

const IMAGE: Record<AssetKind, { label: string; hint: string }> = {
  logo: { label: "Logo", hint: "Square. The brand tile on phones and beside the organisation name." },
  logo_wide: { label: "Wide logo", hint: "Mark and wordmark, shown on a white card at the top of the sidebar." },
  favicon: { label: "Favicon", hint: "The browser tab icon. Square, 32–64 px; ICO or PNG." },
};

function draftFrom(site: SiteBranding, title: string | null, orgName: string | null): Draft {
  return {
    title: title ?? "",
    orgName: orgName ?? "",
    colors: { ...DEFAULT_COLORS, ...site.colors },
    images: { logo: site.logoUrl, logo_wide: site.logoWideUrl, favicon: site.faviconUrl },
    uploads: {},
  };
}

/** What the draft would look like once saved. */
function siteOf(d: Draft): SiteBranding {
  const colors: Partial<Record<ColorKey, string>> = {};
  for (const t of COLOR_TOKENS) if (d.colors[t.key].toLowerCase() !== t.default) colors[t.key] = d.colors[t.key].toLowerCase();
  return {
    productTitle: d.title.trim() || DEFAULT_TITLE,
    staffOrgName: d.orgName.trim() || ORG_NAME,
    logoUrl: d.images.logo,
    logoWideUrl: d.images.logo_wide,
    faviconUrl: d.images.favicon,
    colors,
  };
}

function same(a: Draft | null, b: Draft | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function BrandingPanel({ live, onOpenDisplay }: { live: boolean; onOpenDisplay?: () => void }) {
  const tab = useBranding();
  const applyToTab = useApplyBranding();
  const [saved, setSaved] = useState<BrandingResponse | null>(null);
  const [initial, setInitial] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const adopt = useCallback((resp: BrandingResponse | null) => {
    const d = draftFrom(resolveBranding(resp), resp?.product_title ?? null, resp?.staff_org_name ?? null);
    setSaved(resp);
    setInitial(d);
    setDraft(d);
  }, []);

  const load = useCallback(() => {
    setLoadError(null);
    api.getBranding()
      .then((r) => adopt(parseBranding(r)))
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : "Could not load the branding."));
  }, [adopt]);

  useEffect(() => {
    if (live) {
      load();
      return;
    }
    // Sample data: start from what this tab wears.
    const d = draftFrom(
      tab,
      tab.productTitle !== DEFAULT_TITLE ? tab.productTitle : null,
      tab.staffOrgName !== ORG_NAME ? tab.staffOrgName : null,
    );
    setInitial(d);
    setDraft(d);
    // Only on mount: later tab changes come from this panel itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, load]);

  const preview = useMemo(() => (draft ? siteOf(draft) : null), [draft]);
  const checks = useMemo(() => (draft ? brandingContrast(draft.colors) : []), [draft]);

  if (!draft || !preview) {
    return (
      <Panel className="px-5 py-4 text-[13px] text-ink-muted">
        {loadError ? (
          <div className="flex items-center gap-3" role="alert">
            <AlertTriangle className="h-4 w-4 text-brand-red-dark" /> {loadError}
            <button onClick={load} className="ml-auto rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-black/[0.08]">
              Try again
            </button>
          </div>
        ) : "Loading the branding…"}
      </Panel>
    );
  }

  const titleError = brandTextError(draft.title);
  const orgError = brandTextError(draft.orgName);
  const dirty = !same(draft, initial);
  const failing = checks.filter((c) => !c.pass);
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  const pick = async (kind: AssetKind, file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_ASSET_BYTES[kind]) {
      toast.error(`${IMAGE[kind].label}: the file is larger than ${MAX_ASSET_BYTES[kind] / 1024} KB.`);
      return;
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    const err = uploadError(kind, bytes);
    const type = sniffImage(bytes);
    if (err || !type) {
      toast.error(`${IMAGE[kind].label}: ${err ?? "unsupported image."}`);
      return;
    }
    const base64 = bytesToBase64(bytes);
    setDraft((d) => d && {
      ...d,
      images: { ...d.images, [kind]: `data:${type};base64,${base64}` },
      uploads: { ...d.uploads, [kind]: { contentType: type, base64 } },
    });
  };

  const save = async () => {
    if (titleError || orgError || busy) return;
    if (!live) {
      applyToTab(preview);
      setInitial({ ...draft, uploads: {} });
      setDraft({ ...draft, uploads: {} });
      toast.info("Sample data: nothing is sent. The new look applies to this tab only.");
      return;
    }
    setBusy(true);
    try {
      let resp: BrandingResponse | null = saved;
      for (const kind of ASSET_KINDS) {
        const up = draft.uploads[kind];
        if (up) resp = parseBranding(await api.uploadBrandingAsset(kind, up.contentType, up.base64));
      }
      resp = parseBranding(await api.saveBranding({
        product_title: draft.title.trim(),
        staff_org_name: draft.orgName.trim(),
        colors: colorPatch(draft.colors, saved?.colors ?? {}),
      }));
      adopt(resp);
      applyToTab(resolveBranding(resp));
      toast.success("Branding saved. Both consoles show it within a minute.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the branding.");
      load(); // show what did get saved
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (busy) return;
    const ask = live
      ? "Reset branding to defaults? This removes the title, organisation name, colours and every uploaded image, for everyone."
      : "Reset this tab's branding to defaults? (Sample data: nothing is sent.)";
    if (!window.confirm(ask)) return;
    if (!live) {
      applyToTab(DEFAULT_BRANDING);
      const d = draftFrom(DEFAULT_BRANDING, null, null);
      setInitial(d);
      setDraft(d);
      toast.info("Sample data: nothing is sent. This tab is back to the default look.");
      return;
    }
    setBusy(true);
    try {
      const resp = parseBranding(await api.resetBranding());
      adopt(resp);
      applyToTab(resolveBranding(resp));
      toast.success("Branding reset to defaults.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reset the branding.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      <div className="min-w-0 space-y-5">
        {/* Names */}
        <Panel className="space-y-4 px-5 py-4">
          <SectionTitle icon={Type} title="Title and name" hint="Shown to staff only, in both consoles." />
          <TextField
            id="brand-title"
            label="Product title"
            hint="The browser tab title."
            value={draft.title}
            placeholder={DEFAULT_TITLE}
            error={titleError}
            disabled={busy}
            onChange={(title) => set({ title })}
          />
          <TextField
            id="brand-org"
            label="Staff organisation name"
            hint="Beside the logo in the sidebar."
            value={draft.orgName}
            placeholder={ORG_NAME}
            error={orgError}
            disabled={busy}
            onChange={(orgName) => set({ orgName })}
          />
          <p className="rounded-xl bg-brand-blue/10 px-3 py-2 text-[12px] text-ink-soft">
            This is not the company name patients hear on calls and read in texts. That name is part of the SMS
            consent wording and is set in{" "}
            {onOpenDisplay ? (
              <button type="button" onClick={onOpenDisplay} className="font-semibold text-brand-blue-dark underline underline-offset-2">
                Display → Company name
              </button>
            ) : (
              <span className="font-semibold">Display → Company name</span>
            )}
            .
          </p>
        </Panel>

        {/* Images */}
        <Panel className="space-y-4 px-5 py-4">
          <SectionTitle
            icon={ImageUp}
            title="Logos and favicon"
            hint="PNG, JPEG, WebP or ICO. Logos up to 512 KB, the favicon up to 128 KB. SVG is not accepted (it can carry scripts)."
          />
          <div className="grid gap-3 sm:grid-cols-3">
            {ASSET_KINDS.map((kind) => (
              <ImageField
                key={kind}
                kind={kind}
                url={draft.images[kind]}
                pending={!!draft.uploads[kind]}
                disabled={busy}
                onPick={(f) => pick(kind, f)}
              />
            ))}
          </div>
        </Panel>

        {/* Colours */}
        <Panel className="space-y-4 px-5 py-4">
          <SectionTitle icon={Palette} title="Colours" hint="Each starts from today's theme. The preview updates as you edit." />
          <div className="grid gap-3 sm:grid-cols-2">
            {COLOR_TOKENS.map((t) => (
              <ColorField
                key={t.key}
                label={t.label}
                hint={t.hint}
                value={draft.colors[t.key]}
                fallback={t.default}
                disabled={busy}
                onChange={(v) => set({ colors: { ...draft.colors, [t.key]: v } })}
              />
            ))}
          </div>
          <ContrastList checks={checks} failing={failing.length} />
        </Panel>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={save}
            disabled={busy || !dirty || !!titleError || !!orgError}
            className="rounded-full bg-brand-deep px-5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => setDraft(initial)}
            disabled={busy || !dirty}
            className="rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-50"
          >
            Discard changes
          </button>
          <button
            type="button"
            onClick={reset}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-brand-red-dark ring-1 ring-brand-red/30 hover:bg-brand-red/5 disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset to defaults
          </button>
          {failing.length && dirty ? (
            <span className="w-full text-[12px] text-ink-muted">You can save with contrast warnings, but some text will be hard to read.</span>
          ) : null}
          {saved?.updated_at && live ? (
            <span className="w-full text-[12px] text-ink-muted">
              Last changed {new Date(saved.updated_at).toLocaleString()}{saved.updated_by ? ` by ${saved.updated_by}` : ""}.
            </span>
          ) : null}
        </div>
      </div>

      <div className="min-w-0">
        <div className="space-y-2 xl:sticky xl:top-6">
          <p className="px-1 text-[12px] font-semibold uppercase tracking-wider text-ink-muted">Preview{dirty ? " · unsaved" : ""}</p>
          <BrandingPreview site={preview} />
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon: Icon, title, hint }: { icon: React.ComponentType<{ className?: string }>; title: string; hint: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <h3 className="font-display text-[15px] font-semibold text-ink">{title}</h3>
        <p className="text-[12px] text-ink-muted">{hint}</p>
      </div>
    </div>
  );
}

function TextField({
  id, label, hint, value, placeholder, error, disabled, onChange,
}: {
  id: string; label: string; hint: string; value: string; placeholder: string;
  error: string | null; disabled?: boolean; onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-[13px] font-semibold text-ink">{label}</label>
      <p className="text-[12px] text-ink-muted">{hint} Empty uses the default.</p>
      <input
        id={id}
        value={value}
        maxLength={80}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={!!error}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-brand/40"
      />
      {error ? <p className="text-[12px] text-brand-red-dark" role="alert">{error}</p> : null}
    </div>
  );
}

function ImageField({
  kind, url, pending, disabled, onPick,
}: {
  kind: AssetKind; url: string; pending: boolean; disabled?: boolean; onPick: (f: File | undefined) => void;
}) {
  const { label, hint } = IMAGE[kind];
  const id = `brand-img-${kind}`;
  return (
    <div className="flex flex-col gap-2 rounded-2xl bg-white/70 p-3 ring-1 ring-black/[0.05]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-ink">{label}</span>
        {pending ? <Pill tone="warn">Not saved</Pill> : null}
      </div>
      <div className={cn("grid h-20 place-items-center overflow-hidden rounded-xl bg-mist/70 ring-1 ring-inset ring-black/[0.05]",
        kind === "favicon" && "h-20")}>
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt={`${label} preview`} className={cn("max-h-full max-w-full object-contain", kind === "favicon" ? "h-8 w-8" : "p-2")} />
        ) : (
          <span className="text-[12px] text-ink-muted">Default</span>
        )}
      </div>
      <p className="text-[11px] leading-snug text-ink-muted">{hint}</p>
      <label
        htmlFor={id}
        className={cn("mt-auto cursor-pointer rounded-full bg-white px-3 py-1.5 text-center text-[12px] font-semibold text-brand-deep ring-1 ring-black/[0.08] hover:bg-mist",
          disabled && "pointer-events-none opacity-50")}
      >
        Choose image…
      </label>
      <input
        id={id}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/x-icon,.ico"
        className="sr-only"
        disabled={disabled}
        onChange={(e) => {
          onPick(e.target.files?.[0]);
          e.target.value = ""; // choosing the same file again still fires
        }}
      />
    </div>
  );
}

function ColorField({
  label, hint, value, fallback, disabled, onChange,
}: {
  label: string; hint: string; value: string; fallback: string; disabled?: boolean; onChange: (v: string) => void;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const changed = value.toLowerCase() !== fallback;
  const id = `brand-color-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div className="flex items-start gap-3 rounded-2xl bg-white/70 p-3 ring-1 ring-black/[0.05]">
      <input
        type="color"
        aria-label={`${label} colour`}
        value={isHex(value) ? value.toLowerCase() : fallback}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.toLowerCase())}
        className="h-10 w-10 shrink-0 cursor-pointer rounded-lg border border-black/[0.08] bg-white p-0.5"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <label htmlFor={id} className="text-[13px] font-semibold text-ink">{label}</label>
          {changed ? (
            <button type="button" disabled={disabled} onClick={() => onChange(fallback)} className="ml-auto text-[11px] font-semibold text-ink-muted hover:text-ink">
              Default
            </button>
          ) : null}
        </div>
        <p className="text-[11px] leading-snug text-ink-muted">{hint}</p>
        <input
          id={id}
          value={text}
          disabled={disabled}
          spellCheck={false}
          maxLength={7}
          aria-invalid={!isHex(text)}
          onChange={(e) => {
            const v = e.target.value.trim();
            setText(v);
            if (isHex(v)) onChange(v.toLowerCase());
          }}
          onBlur={() => setText(value)}
          className={cn("mt-1 h-8 w-28 rounded-lg border bg-white px-2 font-mono text-[12px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40",
            isHex(text) ? "border-black/[0.08]" : "border-brand-red")}
        />
      </div>
    </div>
  );
}

function ContrastList({ checks, failing }: { checks: ReturnType<typeof brandingContrast>; failing: number }) {
  return (
    <div className={cn("space-y-2 rounded-2xl px-3 py-3 ring-1", failing ? "bg-brand-orange/10 ring-brand-orange/35" : "bg-brand/5 ring-brand/15")}>
      <div className="flex items-center gap-2 text-[13px] font-semibold text-ink" role={failing ? "alert" : undefined}>
        {failing ? <AlertTriangle className="h-4 w-4 text-brand-coral-dark" /> : <CheckCircle2 className="h-4 w-4 text-brand" />}
        {failing
          ? `${failing} text ${failing === 1 ? "pair fails" : "pairs fail"} WCAG AA contrast`
          : "All text passes WCAG AA contrast"}
      </div>
      <ul className="grid gap-1 sm:grid-cols-2">
        {checks.map((c) => (
          <li key={c.id} className="flex items-center gap-2 text-[12px]">
            <span
              className="grid h-5 w-7 shrink-0 place-items-center rounded font-bold"
              style={{ background: c.bg, color: c.fg, boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.08)" }}
              aria-hidden
            >
              Aa
            </span>
            <span className={cn("min-w-0 flex-1 truncate", c.pass ? "text-ink-soft" : "font-semibold text-ink")} title={c.label}>{c.label}</span>
            <span className={cn("shrink-0 tabular-nums", c.pass ? "text-ink-muted" : "font-semibold text-brand-red-dark")}>
              {c.ratio.toFixed(1)}:1{c.pass ? "" : ` (needs ${c.min}:1)`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A miniature of the nurse console in the draft's branding: the browser tab
 * (favicon and title), the sidebar (logo, organisation name, nav), the page
 * header, sample badges and buttons. The draft's colours are set as CSS
 * variables on this element only, so the real console is untouched until Save.
 */
export function BrandingPreview({ site }: { site: SiteBranding }) {
  const vars = brandingVars(site.colors, true) as React.CSSProperties;
  return (
    <div style={vars} className="overflow-hidden rounded-[22px] bg-white shadow-lg ring-1 ring-black/[0.06]" aria-label="Branding preview">
      {/* the browser tab */}
      <div className="flex items-center gap-2 bg-mist px-3 py-2">
        <span className="flex min-w-0 max-w-[85%] items-center gap-2 rounded-t-lg bg-white px-3 py-1.5 text-[11px] text-ink shadow-sm">
          {site.faviconUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={site.faviconUrl} alt="" className="h-3.5 w-3.5 shrink-0 object-contain" />
          ) : (
            <span className="h-3.5 w-3.5 shrink-0 rounded-sm bg-mist-cool" aria-hidden />
          )}
          <span className="truncate">{site.productTitle}</span>
        </span>
      </div>

      <div className="flex gap-3 bg-console p-3">
        {/* sidebar */}
        <div className="relative flex w-[8.5rem] shrink-0 flex-col overflow-hidden rounded-2xl bg-gradient-to-b from-brand-deep to-brand-deep-dark p-2.5 text-white">
          <div className="pointer-events-none absolute -left-8 -top-8 h-24 w-24 rounded-full bg-brand-bright/30 blur-2xl" />
          {site.logoWideUrl ? (
            <div className="relative rounded-lg bg-white px-2 py-1.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={site.logoWideUrl} alt={site.staffOrgName} className="mx-auto h-6 max-w-full object-contain" />
            </div>
          ) : (
            <div className="relative flex items-center gap-1.5">
              <div className="grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-lg bg-white/95">
                {site.logoUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={site.logoUrl} alt="" className="h-full w-full object-cover" />
                ) : (
                  <span className="h-3.5 w-2.5 rounded-full bg-brand-yellow" aria-hidden />
                )}
              </div>
              <span className="min-w-0 truncate font-display text-[10px] font-semibold leading-tight">{site.staffOrgName}</span>
            </div>
          )}
          <div className="relative mt-1 text-[9px] text-white/60">Post-op nurse console</div>
          <div className="relative mt-3 space-y-1">
            <div className="rounded-lg bg-white px-2 py-1.5 text-[10px] font-semibold text-brand-deep shadow">Call queue</div>
            <div className="rounded-lg px-2 py-1.5 text-[10px] font-semibold text-white/75">Call status</div>
            <div className="rounded-lg px-2 py-1.5 text-[10px] font-semibold text-white/75">Analytics</div>
          </div>
        </div>

        {/* page */}
        <div className="min-w-0 flex-1 space-y-2.5">
          <div>
            <p className="text-[10px] font-medium text-ink-muted">Post-op nurse console</p>
            <p className="font-display text-[16px] font-semibold leading-tight text-ink">Call status</p>
          </div>
          <div className="space-y-2 rounded-xl bg-white/80 p-2.5 shadow-sm ring-1 ring-black/[0.04]">
            <div className="flex items-center gap-2">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-brand-coral/15 font-display text-[10px] font-semibold text-brand-coral-dark">JR</span>
              <div className="min-w-0">
                <p className="truncate text-[11px] font-semibold text-ink">Jordan Rivera</p>
                <p className="truncate text-[10px] text-ink-muted">Knee arthroscopy · day 2</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-1">
              <Pill tone="emergency" className="px-2 py-0.5 text-[10px]">Emergency</Pill>
              <Pill tone="urgent" className="px-2 py-0.5 text-[10px]">Urgent</Pill>
              <Pill tone="routine" className="px-2 py-0.5 text-[10px]">Routine</Pill>
              <Pill tone="info" className="px-2 py-0.5 text-[10px]">Texted</Pill>
              <Pill tone="good" className="px-2 py-0.5 text-[10px]">Signed off</Pill>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-brand px-2.5 py-1 text-[10px] font-bold text-white">Sign off</span>
              <span className="rounded-full bg-brand-deep px-2.5 py-1 text-[10px] font-semibold text-white">Call now</span>
              <span className="rounded-full bg-brand-red px-2.5 py-1 text-[10px] font-bold text-white">Escalate</span>
              <span className="text-[10px] font-semibold text-brand underline underline-offset-2">Transcript</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
