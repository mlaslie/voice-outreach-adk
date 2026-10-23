"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Pointer, ancestors, childPointer, containerPointers } from "@/lib/json-pointer";
import { cn } from "@/lib/utils";

/**
 * A fully collapsible JSON tree, with no dependencies. Every object and
 * array node opens and closes; values are coloured by type; array indices
 * show; long strings are cut with "more". Nodes are addressed by JSON Pointer
 * (lib/json-pointer.ts), so a caller can expand, highlight, badge, grey or
 * flag any node, and scroll it into view.
 *
 * Expansion is controlled (`expanded`), so "expand all" / "collapse all" and
 * auto-expanding a highlighted path live with the caller: `useTreeExpansion`.
 */

type JsonValue = unknown;

export interface JsonTreeProps {
  value: JsonValue;
  /** The open containers, by pointer ("" is the root). */
  expanded: ReadonlySet<Pointer>;
  onToggle: (p: Pointer) => void;
  /** Clicking a node's key selects it (traceability). */
  onSelect?: (p: Pointer) => void;
  selected?: Pointer | null;
  /** Nodes to highlight (the selection's counterparts). */
  highlighted?: ReadonlySet<Pointer>;
  /** A small "← label" badge after a node. */
  badges?: ReadonlyMap<Pointer, string>;
  /** Greyed nodes, with the reason as a tooltip (e.g. skipped answers). */
  muted?: ReadonlyMap<Pointer, string>;
  /** Red nodes, with the message as a tooltip (e.g. FHIR errors). */
  flagged?: ReadonlyMap<Pointer, string>;
  /** Changing this scrolls the first highlighted (else the selected) node into view. */
  scrollKey?: number;
  /** Strings longer than this are cut, with "more". */
  maxString?: number;
  rootLabel?: string;
  className?: string;
  "aria-label"?: string;
}

/** Expansion state for one tree: toggle, expand/collapse all, open the path to some nodes. */
export function useTreeExpansion(initial: Iterable<Pointer> = [""]) {
  const [expanded, setExpanded] = useState<Set<Pointer>>(() => new Set(initial));
  const toggle = useCallback((p: Pointer) => {
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });
  }, []);
  const expandAll = useCallback((doc: unknown) => setExpanded(new Set(containerPointers(doc))), []);
  const collapseAll = useCallback(() => setExpanded(new Set()), []);
  /** Open every ancestor of `pointers` (so they show), keeping what is open. */
  const reveal = useCallback((pointers: Pointer[]) => {
    setExpanded((s) => {
      const n = new Set(s);
      for (const p of pointers) for (const a of ancestors(p)) n.add(a);
      return n;
    });
  }, []);
  /** Open these to a depth (e.g. the first two levels). */
  const expandDepth = useCallback((doc: unknown, depth: number) => {
    setExpanded(new Set(containerPointers(doc).filter((p) => (p === "" ? 0 : p.split("/").length - 1) < depth)));
  }, []);
  return { expanded, setExpanded, toggle, expandAll, collapseAll, reveal, expandDepth };
}

function isContainer(v: unknown): v is Record<string, unknown> | unknown[] {
  return v !== null && typeof v === "object";
}

/** "QuestionnaireResponse", `linkId "2.3"`: a hint shown on a container's line. */
function hintOf(v: unknown): string | null {
  if (!isContainer(v) || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.resourceType === "string") return o.resourceType;
  if (typeof o.linkId === "string") return `linkId "${o.linkId}"`;
  return null;
}

function summaryOf(v: Record<string, unknown> | unknown[]): string {
  if (Array.isArray(v)) return `[${v.length}]`;
  const n = Object.keys(v).length;
  return `{${n} ${n === 1 ? "key" : "keys"}}`;
}

interface Ctx extends Omit<JsonTreeProps, "value" | "className" | "aria-label" | "rootLabel" | "scrollKey"> {
  maxString: number;
  openStrings: ReadonlySet<Pointer>;
  toggleString: (p: Pointer) => void;
}

function Scalar({ v, p, ctx }: { v: unknown; p: Pointer; ctx: Ctx }) {
  if (typeof v === "string") {
    const long = v.length > ctx.maxString;
    const open = ctx.openStrings.has(p);
    const shown = long && !open ? `${v.slice(0, ctx.maxString)}…` : v;
    return (
      <span className="min-w-0 break-all text-brand-dark">
        &quot;{shown}&quot;
        {long ? (
          <button type="button" onClick={() => ctx.toggleString(p)} className="ml-1 rounded px-1 text-[11px] font-semibold text-brand-blue-dark underline-offset-2 hover:underline">
            {open ? "less" : `more (${v.length})`}
          </button>
        ) : null}
      </span>
    );
  }
  if (typeof v === "number") return <span className="text-brand-blue-dark">{String(v)}</span>;
  if (typeof v === "boolean") return <span className="text-brand-pink-dark">{String(v)}</span>;
  if (v === null) return <span className="italic text-ink-muted">null</span>;
  return <span className="text-ink-soft">{String(v)}</span>;
}

const Node = memo(function Node({ k, v, p, depth, ctx, isIndex }: { k: string | null; v: unknown; p: Pointer; depth: number; ctx: Ctx; isIndex: boolean }) {
  const container = isContainer(v);
  const open = container && ctx.expanded.has(p);
  const hl = ctx.highlighted?.has(p);
  const sel = ctx.selected === p;
  const muted = ctx.muted?.get(p);
  const flag = ctx.flagged?.get(p);
  const badge = ctx.badges?.get(p);
  const hint = container ? hintOf(v) : null;
  const title = [flag ? `FHIR error: ${flag}` : null, muted ? `Not sent: ${muted}` : null].filter(Boolean).join("\n") || undefined;

  const keyEl = k === null ? null : (
    <button
      type="button"
      onClick={() => ctx.onSelect?.(p)}
      className={cn(
        "shrink-0 rounded px-0.5 text-left font-semibold",
        isIndex ? "text-ink-muted" : "text-ink",
        ctx.onSelect ? "cursor-pointer hover:bg-brand/10" : "cursor-default",
      )}
      aria-label={ctx.onSelect ? `Select ${isIndex ? `index ${k}` : k}` : undefined}
    >
      {isIndex ? `[${k}]` : k}
      <span className="font-normal text-ink-muted">:</span>
    </button>
  );

  return (
    <li role="treeitem" aria-expanded={container ? open : undefined} aria-selected={sel || undefined} className="min-w-0">
      <div
        data-pointer={p}
        title={title}
        className={cn(
          "flex min-w-0 flex-wrap items-baseline gap-x-1.5 rounded-md px-1 py-[1px]",
          hl && "bg-brand-yellow/40 ring-1 ring-brand-yellow",
          sel && "bg-brand-turquoise/25 ring-1 ring-brand-turquoise",
          muted && "opacity-50",
          flag && "bg-brand-red/10 ring-1 ring-brand-red/50",
        )}
        style={{ paddingLeft: depth * 14 }}
      >
        {container ? (
          <button type="button" onClick={() => ctx.onToggle(p)} className="grid h-4 w-4 shrink-0 place-items-center self-center rounded text-ink-soft hover:bg-mist" aria-label={open ? "Collapse" : "Expand"}>
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        {keyEl}
        {container ? (
          <button type="button" onClick={() => ctx.onToggle(p)} className="text-ink-muted hover:text-ink">
            {summaryOf(v as Record<string, unknown> | unknown[])}
            {hint ? <span className="ml-1.5 font-semibold text-brand-deep">{hint}</span> : null}
          </button>
        ) : (
          <Scalar v={v} p={p} ctx={ctx} />
        )}
        {badge ? <span className="rounded-full bg-brand/10 px-1.5 text-[10.5px] font-semibold text-brand-dark ring-1 ring-brand/20">← {badge}</span> : null}
        {muted ? <span className="rounded-full bg-mist px-1.5 text-[10.5px] font-semibold text-ink-soft">not sent</span> : null}
        {flag ? <span className="rounded-full bg-brand-red/10 px-1.5 text-[10.5px] font-semibold text-brand-red-dark">error</span> : null}
      </div>
      {open ? (
        <ul role="group" className="min-w-0">
          {(Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as Record<string, unknown>)).map(([ck, cv]) => (
            <Node key={ck} k={ck} v={cv} p={childPointer(p, ck)} depth={depth + 1} ctx={ctx} isIndex={Array.isArray(v)} />
          ))}
          {(Array.isArray(v) ? v.length : Object.keys(v as object).length) === 0 ? (
            <li className="text-ink-muted" style={{ paddingLeft: (depth + 1) * 14 + 24 }}>{Array.isArray(v) ? "(empty array)" : "(empty object)"}</li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
});

/**
 * Scroll ``el`` to the middle of its nearest scrollable ancestor (the pane).
 * Not ``scrollIntoView({behavior: "smooth"})``: Chrome runs one smooth
 * scrollIntoView at a time, so when both panes reveal a node at once the
 * second cancels the first and one pane never moves. Scrolling the pane's own
 * container avoids that, and never scrolls the page.
 */
function revealInScroller(el: HTMLElement): void {
  let pane: HTMLElement | null = el.parentElement;
  while (pane) {
    const oy = getComputedStyle(pane).overflowY;
    if ((oy === "auto" || oy === "scroll") && pane.scrollHeight > pane.clientHeight) break;
    pane = pane.parentElement;
  }
  if (!pane) return;
  const delta = el.getBoundingClientRect().top - pane.getBoundingClientRect().top;
  const top = pane.scrollTop + delta - pane.clientHeight / 2 + el.offsetHeight / 2;
  pane.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

export function JsonTree({
  value,
  expanded,
  onToggle,
  onSelect,
  selected = null,
  highlighted,
  badges,
  muted,
  flagged,
  scrollKey,
  maxString = 80,
  rootLabel,
  className,
  "aria-label": ariaLabel,
}: JsonTreeProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [openStrings, setOpenStrings] = useState<Set<Pointer>>(new Set());
  const toggleString = useCallback((p: Pointer) => {
    setOpenStrings((s) => {
      const n = new Set(s);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });
  }, []);

  useEffect(() => {
    if (scrollKey === undefined || !ref.current) return;
    // After the reveal has rendered: the first highlighted node, else the selected one.
    const id = requestAnimationFrame(() => {
      const first = highlighted && highlighted.size ? [...highlighted][0] : selected;
      if (first === null || first === undefined) return;
      const el = ref.current?.querySelector(`[data-pointer="${CSS.escape(first)}"]`) as HTMLElement | null;
      if (el) revealInScroller(el);
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollKey]);

  const ctx: Ctx = { expanded, onToggle, onSelect, selected, highlighted, badges, muted, flagged, maxString, openStrings, toggleString };
  return (
    <div ref={ref} className={cn("min-w-0 font-mono text-[12px] leading-[1.55]", className)}>
      <ul role="tree" aria-label={ariaLabel} className="min-w-0">
        {isContainer(value) ? (
          <Node k={rootLabel ?? null} v={value} p="" depth={0} ctx={ctx} isIndex={false} />
        ) : (
          <li className="px-1">
            <Scalar v={value} p="" ctx={ctx} />
          </li>
        )}
      </ul>
    </div>
  );
}
