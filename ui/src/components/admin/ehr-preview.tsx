"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ChevronsDownUp, ChevronsUpDown, Copy, Download, FlaskConical, Loader2, Play, Server } from "lucide-react";
import { JsonTree, useTreeExpansion } from "@/components/json-tree";
import { Pointer } from "@/lib/json-pointer";
import {
  EhrPreview,
  EhrPreviewSources,
  EhrSimulation,
  PreviewSource,
  Side,
  counterparts,
  errorsByTarget,
  skippedBySource,
  targetBadges,
} from "@/lib/ehr-export";
import { saveJsonFile } from "@/lib/ehr-export-client";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, Note } from "./ehr-ui";

/**
 * The §4.2 preview: the app's output (the outbox record) and the FHIR bundle
 * the profile makes of it, side by side, each a collapsible JSON tree, with
 * the engine's source map linking the two.
 */

function copyJson(value: unknown, what: string) {
  const text = JSON.stringify(value, null, 2);
  navigator.clipboard?.writeText(text).then(
    () => toast.success(`${what} copied.`),
    () => toast.error("Could not copy: the browser refused clipboard access."),
  );
}

function PaneTools({ onExpand, onCollapse, onCopy, onDownload }: { onExpand: () => void; onCollapse: () => void; onCopy: () => void; onDownload: () => void }) {
  const b = "grid h-7 w-7 place-items-center rounded-full text-ink-soft ring-1 ring-black/[0.06] hover:bg-white hover:text-ink";
  return (
    <div className="flex shrink-0 items-center gap-1">
      <button type="button" className={b} onClick={onExpand} title="Expand all" aria-label="Expand all">
        <ChevronsUpDown className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={b} onClick={onCollapse} title="Collapse all" aria-label="Collapse all">
        <ChevronsDownUp className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={b} onClick={onCopy} title="Copy JSON" aria-label="Copy JSON">
        <Copy className="h-3.5 w-3.5" />
      </button>
      <button type="button" className={b} onClick={onDownload} title="Download JSON" aria-label="Download JSON">
        <Download className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function PaneFrame({ title, tools, children, busy }: { title: ReactNode; tools: ReactNode; children: ReactNode; busy?: boolean }) {
  return (
    <section className="flex min-w-0 flex-col rounded-2xl bg-white/80 ring-1 ring-black/[0.05]">
      <header className="flex min-w-0 flex-wrap items-center gap-2 border-b border-rule/70 px-3 py-2">
        <h4 className="min-w-0 flex-1 text-[12.5px] font-semibold text-ink">{title}</h4>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-muted" aria-label="Updating" /> : null}
        {tools}
      </header>
      <div className="max-h-[70vh] min-w-0 overflow-auto px-2 py-2">{children}</div>
    </section>
  );
}

/** The mapped FHIR bundle alone, with badges and error flags: the Mapping tab's live preview. */
export function FhirPane({ preview, busy, error }: { preview: EhrPreview | null; busy?: boolean; error?: string | null }) {
  const tree = useTreeExpansion();
  const seeded = useRef(false);
  useEffect(() => {
    if (preview?.bundle && !seeded.current) {
      seeded.current = true;
      tree.expandDepth(preview.bundle, 5);
    }
  }, [preview, tree]);
  const badges = useMemo(() => targetBadges(preview?.source_map ?? []), [preview]);
  const flagged = useMemo(() => errorsByTarget(preview?.errors ?? [], preview?.bundle ?? null), [preview]);
  return (
    <PaneFrame
      title="Live preview: mapped FHIR bundle"
      busy={busy}
      tools={
        preview?.bundle ? (
          <PaneTools
            onExpand={() => tree.expandAll(preview.bundle)}
            onCollapse={tree.collapseAll}
            onCopy={() => copyJson(preview.bundle, "Bundle")}
            onDownload={() => saveJsonFile("fhir-bundle.json", preview.bundle)}
          />
        ) : null
      }
    >
      {error ? <Note tone="error">{error}</Note> : null}
      {preview && !preview.bundle ? <Note tone="warn">{preview.errors.map((e) => e.message).join("; ") || "No bundle."}</Note> : null}
      {preview?.bundle ? <JsonTree value={preview.bundle} expanded={tree.expanded} onToggle={tree.toggle} badges={badges} flagged={flagged} aria-label="Mapped FHIR bundle" /> : null}
      {!preview && !error ? <p className="px-2 py-6 text-center text-[12.5px] text-ink-muted">Building the preview…</p> : null}
    </PaneFrame>
  );
}

function SummaryStrip({ preview, onPick }: { preview: EhrPreview; onPick: (side: Side, p: Pointer) => void }) {
  const [open, setOpen] = useState<"skipped" | "warnings" | "errors" | null>(null);
  const items: { key: "skipped" | "warnings" | "errors"; label: string; n: number; tone: string }[] = [
    { key: "skipped", label: "Skipped", n: preview.skipped.length, tone: "text-ink-soft" },
    { key: "warnings", label: "Warnings", n: preview.warnings.length, tone: "text-brand-orange" },
    { key: "errors", label: "FHIR errors", n: preview.errors.length, tone: "text-brand-red-dark" },
  ];
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap gap-2">
        {items.map((it) => (
          <button
            key={it.key}
            type="button"
            aria-expanded={open === it.key}
            onClick={() => setOpen(open === it.key ? null : it.key)}
            className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold ring-1 ring-black/[0.06]", open === it.key ? "bg-brand-deep text-white" : "bg-white/80", it.n && open !== it.key ? it.tone : "")}
          >
            {it.label} ({it.n})
          </button>
        ))}
      </div>
      {open ? (
        <div className="mt-2 rounded-xl bg-white/80 p-3 text-[12.5px] ring-1 ring-black/[0.05]">
          {open === "skipped" ? (
            preview.skipped.length ? (
              <ul className="space-y-1">
                {preview.skipped.map((s, i) => (
                  <li key={i} className="min-w-0 break-words">
                    <button type="button" className="font-mono font-semibold text-brand-deep underline-offset-2 hover:underline disabled:no-underline" disabled={!s.source} onClick={() => s.source && onPick("source", s.source)}>
                      {s.question_id}
                    </button>{" "}
                    <span className="text-ink-soft">{s.reason}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted">Every answer was sent.</p>
            )
          ) : null}
          {open === "warnings" ? (preview.warnings.length ? <ul className="list-disc space-y-1 pl-4">{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : <p className="text-ink-muted">No warnings.</p>) : null}
          {open === "errors" ? (
            preview.errors.length ? (
              <ul className="space-y-1">
                {preview.errors.map((e, i) => (
                  <li key={i} className="min-w-0 break-words text-brand-red-dark">
                    {e.path ? <span className="mr-1 font-mono">{e.path}</span> : null}
                    {e.message}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted">The bundle passes the FHIR R4 structure check.</p>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Both panes, linked by the source map. */
export function TracePanes({ preview, busy }: { preview: EhrPreview; busy?: boolean }) {
  const left = useTreeExpansion();
  const right = useTreeExpansion();
  const seeded = useRef(false);
  const [sel, setSel] = useState<{ side: Side; p: Pointer } | null>(null);
  const [scrollKey, setScrollKey] = useState(0);

  useEffect(() => {
    if (seeded.current || !preview.bundle) return;
    seeded.current = true;
    left.expandDepth(preview.source_record, 3);
    right.expandDepth(preview.bundle, 4);
  }, [preview, left, right]);

  const match = useMemo(() => (sel ? counterparts(preview.source_map, sel.side, sel.p) : null), [sel, preview.source_map]);
  const leftHl = useMemo(() => new Set(match ? (sel!.side === "source" ? match.own : match.pointers) : []), [match, sel]);
  const rightHl = useMemo(() => new Set(match ? (sel!.side === "target" ? match.own : match.pointers) : []), [match, sel]);
  const badges = useMemo(() => targetBadges(preview.source_map), [preview.source_map]);
  const muted = useMemo(() => skippedBySource(preview.skipped), [preview.skipped]);
  const flagged = useMemo(() => errorsByTarget(preview.errors, preview.bundle), [preview.errors, preview.bundle]);

  const pick = useCallback(
    (side: Side, p: Pointer) => {
      if (sel && sel.side === side && sel.p === p) {
        setSel(null);
        return;
      }
      const m = counterparts(preview.source_map, side, p);
      setSel({ side, p });
      const leftPts = side === "source" ? [p, ...m.own] : m.pointers;
      const rightPts = side === "target" ? [p, ...m.own] : m.pointers;
      left.reveal(leftPts);
      right.reveal(rightPts);
      setScrollKey((k) => k + 1);
    },
    [sel, preview.source_map, left, right],
  );

  const expandBoth = () => {
    left.expandAll(preview.source_record);
    right.expandAll(preview.bundle);
  };
  const collapseBoth = () => {
    left.collapseAll();
    right.collapseAll();
  };

  const linkNote = match && sel ? (match.links.length ? match.links.map((l) => l.note ?? (l.question_id ? `answer to ${l.question_id}` : "")).filter(Boolean) : []) : null;

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" onClick={expandBoth}>
          <ChevronsUpDown className="mr-1.5 h-3.5 w-3.5" /> Expand all
        </Button>
        <Button variant="secondary" size="sm" onClick={collapseBoth}>
          <ChevronsDownUp className="mr-1.5 h-3.5 w-3.5" /> Collapse all
        </Button>
        <p className="min-w-0 flex-1 text-[12px] text-ink-muted">
          {sel
            ? match && match.pointers.length
              ? `Linked: ${match.pointers.length} node${match.pointers.length === 1 ? "" : "s"} on the other side${linkNote && linkNote.length ? ` (${[...new Set(linkNote)].join("; ")})` : ""}. Click it again to clear.`
              : "Nothing on the other side comes from this node."
            : "Click a key on either side to see where it comes from or goes."}
        </p>
      </div>
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">
        <PaneFrame
          title="App output (outbox record)"
          tools={<PaneTools onExpand={() => left.expandAll(preview.source_record)} onCollapse={left.collapseAll} onCopy={() => copyJson(preview.source_record, "Outbox record")} onDownload={() => saveJsonFile("outbox-record.json", preview.source_record)} />}
        >
          <JsonTree
            value={preview.source_record}
            expanded={left.expanded}
            onToggle={left.toggle}
            onSelect={(p) => pick("source", p)}
            selected={sel?.side === "source" ? sel.p : null}
            highlighted={leftHl}
            muted={muted}
            scrollKey={sel?.side === "target" ? scrollKey : undefined}
            aria-label="App output (outbox record)"
          />
        </PaneFrame>
        <PaneFrame
          title="Mapped FHIR bundle"
          busy={busy}
          tools={preview.bundle ? <PaneTools onExpand={() => right.expandAll(preview.bundle)} onCollapse={right.collapseAll} onCopy={() => copyJson(preview.bundle, "Bundle")} onDownload={() => saveJsonFile("fhir-bundle.json", preview.bundle)} /> : null}
        >
          {preview.bundle ? (
            <JsonTree
              value={preview.bundle}
              expanded={right.expanded}
              onToggle={right.toggle}
              onSelect={(p) => pick("target", p)}
              selected={sel?.side === "target" ? sel.p : null}
              highlighted={rightHl}
              badges={badges}
              flagged={flagged}
              scrollKey={sel?.side === "source" ? scrollKey : undefined}
              aria-label="Mapped FHIR bundle"
            />
          ) : (
            <Note tone="warn">{preview.errors.map((e) => e.message).join("; ") || "No bundle for this source."}</Note>
          )}
        </PaneFrame>
      </div>
      <SummaryStrip preview={preview} onPick={pick} />
    </div>
  );
}

export function sourceKey(s: PreviewSource | null): string {
  if (!s) return "";
  return "sample" in s ? `sample:${s.sample}` : `call:${s.call_id}`;
}

export function parseSourceKey(k: string): PreviewSource | null {
  if (k.startsWith("sample:")) return { sample: k.slice(7) };
  if (k.startsWith("call:")) return { call_id: k.slice(5) };
  return null;
}

export function SourcePicker({ sources, value, onChange }: { sources: EhrPreviewSources | null; value: PreviewSource | null; onChange: (s: PreviewSource) => void }) {
  return (
    <label className="flex min-w-0 flex-wrap items-center gap-2 text-[12.5px] font-semibold text-ink-soft">
      Source
      <select
        className="h-8 min-w-0 max-w-full flex-1 rounded-lg border border-rule bg-white px-2 text-[13px] font-normal text-ink sm:flex-none"
        value={sourceKey(value)}
        onChange={(e) => {
          const s = parseSourceKey(e.target.value);
          if (s) onChange(s);
        }}
      >
        {!value ? <option value="">Choose a source…</option> : null}
        {sources?.samples.length ? (
          <optgroup label="Fictional samples">
            {sources.samples.map((s) => (
              <option key={s.campaign_id} value={`sample:${s.campaign_id}`}>
                {s.label}
              </option>
            ))}
          </optgroup>
        ) : null}
        {sources?.calls.length ? (
          <optgroup label="Recent signed-off calls">
            {sources.calls.map((c) => (
              <option key={c.call_id} value={`call:${c.call_id}`}>
                {c.label} · {c.campaign_id} · {c.status}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
    </label>
  );
}

export function SimulatePanel({ run }: { run: () => Promise<EhrSimulation> }) {
  const [result, setResult] = useState<EhrSimulation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tree = useTreeExpansion();
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await run();
      setResult(r);
      tree.expandDepth(r.resources, 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The simulation failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      title="Simulate export"
      icon={<Server className="h-4 w-4 text-brand-deep" />}
      actions={
        <Button size="sm" onClick={go} disabled={busy}>
          {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
          Simulate export
        </Button>
      }
    >
      <p className="text-[12.5px] text-ink-soft">Runs against a built-in fake FHIR server; nothing leaves the system. The second run re-sends the record: it updates the same resources, nothing new is created.</p>
      {error ? <div className="mt-3"><Note tone="error">{error}</Note></div> : null}
      {result ? (
        <div className="mt-3 space-y-3">
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            {result.runs.map((r, i) => (
              <div key={i} className="min-w-0 rounded-xl bg-white/80 p-3 ring-1 ring-black/[0.05]">
                <p className="mb-2 text-[12.5px] font-semibold text-ink">
                  Run {i + 1}: <span className="text-brand-dark">{r.created} created</span>, <span className="text-brand-blue-dark">{r.updated} updated</span>
                  {r.outcome?.status ? <span className={cn("ml-2 rounded-full px-1.5 py-0.5 text-[10.5px]", r.outcome.status === "exported" ? "bg-brand/10 text-brand-dark" : "bg-brand-red/10 text-brand-red-dark")}>{r.outcome.status}</span> : null}
                </p>
                {r.outcome?.error ? <p className="mb-2 break-words text-[12px] text-brand-red-dark">{r.outcome.error}</p> : null}
                {r.created_refs?.length || r.updated_refs?.length ? (
                  <p className="mb-2 break-words font-mono text-[11px] text-ink-muted">
                    {r.created_refs?.length ? `created ${r.created_refs.join(", ")}` : ""}
                    {r.created_refs?.length && r.updated_refs?.length ? " · " : ""}
                    {r.updated_refs?.length ? `updated ${r.updated_refs.join(", ")}` : ""}
                  </p>
                ) : null}
                <ul className="space-y-1 font-mono text-[11.5px]">
                  {r.requests.map((q, j) => (
                    <li key={j} className="flex min-w-0 items-start gap-2">
                      <span className="w-11 shrink-0 font-semibold text-ink">{q.method}</span>
                      <span className="min-w-0 flex-1 break-all text-ink-soft">{q.url}</span>
                      <span className={cn("shrink-0 font-semibold", q.status < 300 ? "text-brand-dark" : "text-brand-red-dark")}>{q.status}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <PaneFrame
            title={`Stored on the fake server (${result.resources.length})`}
            tools={<PaneTools onExpand={() => tree.expandAll(result.resources)} onCollapse={tree.collapseAll} onCopy={() => copyJson(result.resources, "Resources")} onDownload={() => saveJsonFile("simulated-resources.json", result.resources)} />}
          >
            <JsonTree value={result.resources} expanded={tree.expanded} onToggle={tree.toggle} aria-label="Stored resources" />
          </PaneFrame>
        </div>
      ) : null}
    </Card>
  );
}

export function PreviewTab({
  preview,
  busy,
  error,
  sources,
  source,
  onSource,
  simulate,
  live,
}: {
  preview: EhrPreview | null;
  busy: boolean;
  error: string | null;
  sources: EhrPreviewSources | null;
  source: PreviewSource | null;
  onSource: (s: PreviewSource) => void;
  simulate: () => Promise<EhrSimulation>;
  live: boolean;
}) {
  return (
    <div className="min-w-0 space-y-4">
      <Card title="Preview: app output and mapped FHIR, side by side" icon={<FlaskConical className="h-4 w-4 text-brand-deep" />}>
        <div className="mb-3 flex min-w-0 flex-wrap items-center gap-3">
          <SourcePicker sources={sources} value={source} onChange={onSource} />
          <p className="min-w-0 text-[12px] text-ink-muted">
            Built from the editor&apos;s current (unsaved) profile. The patient reference is a placeholder: a preview never looks anyone up.
            {live ? " Nothing is stored." : ""}
          </p>
        </div>
        {error ? <Note tone="error">{error}</Note> : null}
        {preview ? <TracePanes key={sourceKey(source)} preview={preview} busy={busy} /> : !error ? <p className="py-8 text-center text-[13px] text-ink-muted">Building the preview…</p> : null}
      </Card>
      <SimulatePanel run={simulate} />
    </div>
  );
}
