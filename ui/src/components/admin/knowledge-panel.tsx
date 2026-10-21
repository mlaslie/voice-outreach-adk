"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ButtonHTMLAttributes, type ComponentType, type ReactNode } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Archive,
  BookOpenCheck,
  CheckCircle2,
  FileText,
  FlaskConical,
  Library,
  Lock,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Send,
  ShieldAlert,
  Timer,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import {
  APPROVED_CHARS_BUDGET,
  CARE_SETTINGS,
  CARE_SETTING_LABEL,
  DEFAULT_MIN_SUPPORT,
  GUIDELINE_FILE_ACCEPT,
  KNOWLEDGE_MODES as MODES,
  KnowledgeDoc,
  KnowledgeDocInput,
  KnowledgeItem,
  KnowledgeItemInput,
  KnowledgeSettings,
  KnowledgeStatus,
  KnowledgeTryContext,
  KnowledgeTryResult,
  MAX_ALT_QUESTIONS,
  MAX_ANSWER,
  MAX_DOCS,
  MAX_DOC_BODY,
  MAX_DOC_TITLE,
  MAX_PROCEDURE,
  MAX_PROCEDURES,
  MAX_QUESTION,
  MIN_SUPPORT_RANGE,
  MODE_HINT,
  MODE_LABEL,
  STATUS_LABEL,
  StatusFilter,
  addProcedures,
  appliesToText,
  approvalOverBudget,
  approvedChars,
  canApprove,
  canDelete,
  canRetire,
  categoryLabelOf,
  clampMinSupport,
  classificationSummary,
  demoCreate,
  demoCreateDoc,
  demoDocs,
  demoEdit,
  demoEditDoc,
  demoItems,
  demoSettings,
  demoTry,
  docDraftError,
  editableCategories,
  filterDocs,
  filterItems,
  guidelineFileError,
  guidelineTextError,
  itemDraftError,
  liveWarnings,
  needsReapproval,
  normaliseFileText,
  normaliseSettings,
  parseAltQuestions,
  reasonText,
  settingsPatch,
  sortDocs,
  sortItems,
  statusCounts,
  titleFromFileName,
  toggleCategory,
} from "@/lib/knowledge";
import { ShadowStats, shadowStats } from "@/lib/patient-questions";
import { CallDoc, CareSetting } from "@/lib/types";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { EmptyState, FilterChips, Panel, Pill, Tone } from "@/components/nurse/parts";
import { SourceChips, SupportScore } from "@/components/knowledge-sources";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Admin → Knowledge base (docs/KNOWLEDGE-PLAN.md v2, step B): the mode (off /
 * shadow / live) and checks that govern answering, "Try it" with the
 * classification, sources and scores, guidelines, and example Q&A. Live:
 * /api/admin/knowledge/* (admin build only, audited). Sample data
 * (?data=demo): fictional content held in this tab, a keyword "Try it",
 * nothing sent.
 */

const DEMO_TOAST = "Sample data: nothing is sent.";

const STATUS_TONE: Record<KnowledgeStatus, Tone> = { draft: "routine", approved: "good", retired: "muted" };

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message : fallback;
}

export function KnowledgePanel({ live, timeZone, calls }: { live: boolean; timeZone?: string; calls?: CallDoc[] }) {
  const [items, setItems] = useState<KnowledgeItem[] | null>(live ? null : demoItems());
  const [docs, setDocs] = useState<KnowledgeDoc[] | null>(live ? null : demoDocs());
  const [settings, setSettings] = useState<KnowledgeSettings | null>(live ? null : demoSettings());
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  /** Entries and guidelines this session sent back to draft by editing them while approved. */
  const [sentBack, setSentBack] = useState<Set<string>>(new Set());
  const [docsSentBack, setDocsSentBack] = useState<Set<string>>(new Set());

  const loadItems = useCallback(() => {
    setItemsError(null);
    api.getKnowledgeItems()
      .then((r) => setItems(Array.isArray(r.items) ? r.items : []))
      .catch((e) => setItemsError(errorText(e, "Could not load the example questions and answers.")));
  }, []);
  const loadDocs = useCallback(() => {
    setDocsError(null);
    api.getKnowledgeDocs()
      .then((r) => setDocs(Array.isArray(r.documents) ? r.documents.map(normaliseDoc) : []))
      .catch((e) => setDocsError(errorText(e, "Could not load the guidelines.")));
  }, []);
  const loadSettings = useCallback(() => {
    setSettingsError(null);
    api.getKnowledgeSettings()
      .then((raw) => setSettings(normaliseSettings(raw)))
      .catch((e) => setSettingsError(errorText(e, "Could not load the knowledge base settings.")));
  }, []);

  useEffect(() => {
    if (!live) return;
    loadItems();
    loadDocs();
    loadSettings();
  }, [live, loadItems, loadDocs, loadSettings]);

  const replace = (item: KnowledgeItem) =>
    setItems((xs) => (xs ? xs.map((x) => (x.item_id === item.item_id ? item : x)) : xs));
  const replaceDoc = (doc: KnowledgeDoc) =>
    setDocs((xs) => (xs ? xs.map((x) => (x.doc_id === doc.doc_id ? doc : x)) : xs));

  const approvedItems = items?.filter((i) => i.status === "approved").length ?? null;
  const approvedDocs = docs?.filter((d) => d.status === "approved").length ?? null;
  const ratings = useMemo(() => (calls ? shadowStats(calls) : null), [calls]);

  return (
    <>
      <SettingsCard
        live={live}
        settings={settings}
        error={settingsError}
        onRetry={loadSettings}
        onSaved={setSettings}
        approvedItems={approvedItems}
        approvedDocs={approvedDocs}
        ratings={ratings}
        timeZone={timeZone}
      />
      <TryItCard live={live} items={items ?? []} docs={docs ?? []} settings={settings} />
      <GuidelinesList
        live={live}
        docs={docs}
        error={docsError}
        onRetry={loadDocs}
        timeZone={timeZone}
        sentBack={docsSentBack}
        onCreated={(d) => setDocs((xs) => [d, ...(xs ?? [])])}
        onChanged={(d, previous) => {
          replaceDoc(d);
          setDocsSentBack((s) => {
            const n = new Set(s);
            if (previous?.status === "approved" && d.status === "draft") n.add(d.doc_id);
            else if (d.status !== "draft") n.delete(d.doc_id);
            return n;
          });
        }}
        onDeleted={(id) => setDocs((xs) => (xs ? xs.filter((x) => x.doc_id !== id) : xs))}
      />
      <QaList
        live={live}
        items={items}
        error={itemsError}
        onRetry={loadItems}
        settings={settings}
        timeZone={timeZone}
        sentBack={sentBack}
        onCreated={(it) => setItems((xs) => [it, ...(xs ?? [])])}
        onChanged={(it, previous) => {
          replace(it);
          setSentBack((s) => {
            const n = new Set(s);
            if (previous?.status === "approved" && it.status === "draft") n.add(it.item_id);
            else if (it.status !== "draft") n.delete(it.item_id);
            return n;
          });
        }}
        onDeleted={(id) => setItems((xs) => (xs ? xs.filter((x) => x.item_id !== id) : xs))}
      />
    </>
  );
}

/** A guideline as listed: arrays always present, chars counted if missing. */
function normaliseDoc(d: KnowledgeDoc): KnowledgeDoc {
  return {
    ...d,
    procedures: Array.isArray(d.procedures) ? d.procedures : [],
    care_settings: Array.isArray(d.care_settings) ? d.care_settings : [],
    chars: typeof d.chars === "number" ? d.chars : (d.body ?? "").length,
  };
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function SettingsCard({
  live,
  settings,
  error,
  onRetry,
  onSaved,
  approvedItems,
  approvedDocs,
  ratings,
  timeZone,
}: {
  live: boolean;
  settings: KnowledgeSettings | null;
  error: string | null;
  onRetry: () => void;
  onSaved: (s: KnowledgeSettings) => void;
  approvedItems: number | null;
  approvedDocs: number | null;
  /** Shadow drafts nurses rated in the loaded calls (null: not known). */
  ratings: ShadowStats | null;
  timeZone?: string;
}) {
  const [draft, setDraft] = useState<KnowledgeSettings | null>(settings);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(settings), [settings]);

  if (!settings || !draft) {
    return (
      <Panel className="px-5 py-4 text-[13px] text-ink-muted">
        {error ? (
          <div className="flex items-center gap-3" role="alert">
            <AlertTriangle className="h-4 w-4 text-brand-red-dark" /> {error}
            <button onClick={onRetry} className="ml-auto rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-black/[0.08]">
              Try again
            </button>
          </div>
        ) : "Loading the knowledge base settings…"}
      </Panel>
    );
  }

  const patch = settingsPatch(settings, draft);
  const dirty = Object.keys(patch).length > 0;
  const locked = draft.locked_categories;
  const chips = [
    ...draft.available_categories.filter((c) => !locked.includes(c.key)),
    ...locked.map((k) => draft.available_categories.find((c) => c.key === k) ?? { key: k, label: categoryLabelOf(k) }),
  ];
  const set = (p: Partial<KnowledgeSettings>) => setDraft((d) => (d ? { ...d, ...p } : d));
  const warnings = liveWarnings({
    approvedItems: approvedItems ?? 1,
    approvedDocs: approvedDocs ?? 0,
    ratings: ratings ? ratings.rated : null,
  });

  const save = async () => {
    if (!dirty || busy) return;
    if (patch.mode === "live") {
      const lines = [
        "Go live?",
        "",
        "On new calls the agent will answer patients' questions itself when every check passes. Medication, symptom and urgent questions still go to a nurse.",
      ];
      if (ratings && ratings.rated > 0) {
        lines.push("", `Shadow ratings in the loaded calls: ${ratings.good} good, ${ratings.notRight} not right (${ratings.rated} rated).`);
      }
      if (warnings.length) lines.push("", ...warnings.map((w) => `• ${w}`));
      if (!window.confirm(lines.join("\n"))) return;
    }
    if (!live) {
      onSaved(draft);
      toast.info(DEMO_TOAST);
      return;
    }
    setBusy(true);
    try {
      const next = normaliseSettings(await api.saveKnowledgeSettings(patch));
      onSaved(next);
      toast.success(
        next.mode === "live"
          ? "Saved. The agent answers questions on new calls when every check passes."
          : next.mode === "shadow"
            ? "Saved. Questions go to a nurse; the agent drafts answers after each call."
            : "Saved. The agent answers no questions.",
      );
    } catch (e) {
      toast.error(errorText(e, "Could not save the settings."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel className="space-y-4 px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
          <BookOpenCheck className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Answering patient questions</h3>
          <p className="text-[12px] text-ink-muted">
            The agent can answer a patient&rsquo;s question from the clinic&rsquo;s approved examples and guidelines, but only
            when the answer is checked against them. Anything else goes to a nurse, as today.
          </p>
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Mode</legend>
        <div role="radiogroup" aria-label="Mode" className="grid gap-2 sm:grid-cols-3">
          {MODES.map((m) => {
            const on = draft.mode === m;
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={on}
                disabled={busy}
                onClick={() => set({ mode: m })}
                className={cn(
                  "rounded-2xl px-4 py-3 text-left ring-1 ring-inset transition-colors disabled:opacity-60",
                  on
                    ? m === "live"
                      ? "bg-brand-deep text-white ring-brand-deep"
                      : "bg-brand/[0.08] ring-brand/40"
                    : "bg-white ring-black/[0.08] hover:ring-black/[0.16]",
                )}
              >
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className={cn(
                      "grid h-4 w-4 place-items-center rounded-full ring-2 ring-inset",
                      on ? (m === "live" ? "ring-white" : "ring-brand-deep") : "ring-ink-muted/50",
                    )}
                  >
                    {on ? <span className={cn("h-2 w-2 rounded-full", m === "live" ? "bg-white" : "bg-brand-deep")} /> : null}
                  </span>
                  <span className={cn("text-[14px] font-semibold", on && m === "live" ? "text-white" : "text-ink")}>{MODE_LABEL[m]}</span>
                  {settings.mode === m ? (
                    <span className={cn("ml-auto text-[11px] font-semibold uppercase tracking-wide", on && m === "live" ? "text-white/80" : "text-ink-muted")}>
                      Current
                    </span>
                  ) : null}
                </span>
                <span className={cn("mt-1 block text-[12px]", on && m === "live" ? "text-white/85" : "text-ink-muted")}>{MODE_HINT[m]}</span>
              </button>
            );
          })}
        </div>
        {draft.mode === "live" && warnings.length ? (
          <ul className="space-y-1 rounded-xl bg-brand-orange/15 px-3 py-2 text-[12px] text-ink" role="status">
            {warnings.map((w) => (
              <li key={w} className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-coral-dark" /> {w}
              </li>
            ))}
          </ul>
        ) : null}
        {draft.mode !== "off" && ratings && ratings.drafts > 0 ? (
          <p className="text-[12px] text-ink-muted">
            Shadow drafts in the loaded calls: {ratings.drafts}, of which nurses rated {ratings.rated}
            {ratings.rated ? ` (${ratings.good} good, ${ratings.notRight} not right)` : ""}.
          </p>
        ) : null}
        {settings.updated_by ? (
          <p className="text-[12px] text-ink-muted">
            Last changed by {settings.updated_by}
            {settings.updated_at ? `, ${fmtDateTime(settings.updated_at, { month: "short", day: "numeric", timeZone })}` : ""}.
          </p>
        ) : null}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Channels</legend>
        <div className="flex flex-wrap gap-3">
          <label className="inline-flex items-center gap-2 rounded-full bg-white px-3.5 py-1.5 text-[13px] font-semibold text-ink ring-1 ring-black/[0.08]">
            <input
              type="checkbox"
              className="h-4 w-4 accent-brand"
              checked={draft.channels.voice}
              disabled={busy}
              onChange={(e) => set({ channels: { ...draft.channels, voice: e.target.checked } })}
            />
            Voice calls
          </label>
          <label className="inline-flex cursor-not-allowed items-center gap-2 rounded-full bg-mist/60 px-3.5 py-1.5 text-[13px] font-semibold text-ink-muted ring-1 ring-black/[0.05]">
            <input type="checkbox" className="h-4 w-4" checked={draft.channels.sms} disabled readOnly />
            Text messages
            <Pill tone="muted">Coming soon</Pill>
          </label>
        </div>
        <p className="text-[12px] text-ink-muted">Texts are not answered yet: the text-message path is not built, so questions by text always go to a nurse.</p>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Topics the agent may answer</legend>
        <div className="flex flex-wrap gap-2">
          {chips.map((c) => {
            const isLocked = locked.includes(c.key);
            const on = !isLocked && draft.allowed_categories.includes(c.key);
            return (
              <button
                key={c.key}
                type="button"
                role="checkbox"
                aria-checked={on}
                aria-disabled={isLocked || busy}
                disabled={isLocked || busy}
                title={isLocked ? "Never answered by the agent" : undefined}
                onClick={() => set({ allowed_categories: toggleCategory(draft.allowed_categories, c.key, locked) })}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                  isLocked
                    ? "cursor-not-allowed bg-brand-red/[0.06] text-brand-red-dark ring-1 ring-inset ring-brand-red/20"
                    : on
                      ? "bg-brand-deep text-white shadow-sm"
                      : "bg-white text-ink-soft ring-1 ring-black/[0.08] hover:text-ink",
                )}
              >
                {isLocked ? <Lock className="h-3.5 w-3.5" /> : on ? <CheckCircle2 className="h-3.5 w-3.5" /> : null}
                {c.label}
                {isLocked ? <span className="font-normal">· never answered by the agent</span> : null}
              </button>
            );
          })}
        </div>
        <p className="text-[12px] text-ink-muted">
          Medication and symptom questions, and anything that sounds urgent, always go to a nurse the same day, whatever is set here.
          Unclear questions go to a nurse too.
        </p>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label htmlFor="kb-min-support" className="flex items-baseline justify-between text-[13px] font-semibold text-ink">
            Minimum support
            <span className="tabular-nums text-ink-soft">{Math.round(draft.min_support * 100)}%</span>
          </label>
          <input
            id="kb-min-support"
            type="range"
            min={MIN_SUPPORT_RANGE.min}
            max={MIN_SUPPORT_RANGE.max}
            step={MIN_SUPPORT_RANGE.step}
            value={draft.min_support}
            disabled={busy}
            onChange={(e) => set({ min_support: clampMinSupport(Number(e.target.value)) })}
            className="w-full accent-brand"
          />
          <p className="text-[12px] text-ink-muted">
            How much of an answer a separate check must find backed by the approved material before the patient hears it.
            Higher defers more. Default {Math.round(DEFAULT_MIN_SUPPORT * 100)}%.
          </p>
        </div>
        <label className="flex items-start gap-3 self-start rounded-2xl bg-mist/50 px-4 py-3 ring-1 ring-inset ring-black/[0.06]">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 accent-brand"
            checked={draft.ask_satisfied}
            disabled={busy}
            onChange={(e) => set({ ask_satisfied: e.target.checked })}
          />
          <span>
            <span className="block text-[13px] font-semibold text-ink">Ask &ldquo;Did that answer your question?&rdquo;</span>
            <span className="block text-[12px] text-ink-muted">A &ldquo;no&rdquo; turns the question into a nurse follow-up.</span>
          </span>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-black/[0.05] pt-3">
        <p className="min-w-0 flex-1 text-[12px] text-ink-muted">
          {live ? "Changes apply to new calls and are recorded in the audit log with your name." : "Sample data: saving changes this tab only."}
        </p>
        <button
          type="button"
          disabled={!dirty || busy}
          onClick={() => setDraft(settings)}
          className="rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-50"
        >
          Discard
        </button>
        <button
          type="button"
          disabled={!dirty || busy}
          onClick={save}
          className="rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Saving…" : patch.mode === "live" ? "Save and go live" : "Save"}
        </button>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Try it
// ---------------------------------------------------------------------------

function TryItCard({
  live,
  items,
  docs,
  settings,
}: {
  live: boolean;
  items: KnowledgeItem[];
  docs: KnowledgeDoc[];
  settings: KnowledgeSettings | null;
}) {
  const [question, setQuestion] = useState("");
  const [procedure, setProcedure] = useState("");
  const [careSetting, setCareSetting] = useState<"" | CareSetting>("");
  const [days, setDays] = useState("");
  const [diabetic, setDiabetic] = useState<"" | "yes" | "no">("");
  const [result, setResult] = useState<{ question: string; context: KnowledgeTryContext; r: KnowledgeTryResult } | null>(null);
  const [busy, setBusy] = useState(false);
  const q = question.trim();
  const daysNum = days.trim() === "" ? undefined : Number(days);
  const daysBad = daysNum !== undefined && (!Number.isInteger(daysNum) || daysNum < 0 || daysNum > 3650);

  const context = (): KnowledgeTryContext => {
    const c: KnowledgeTryContext = {};
    if (procedure.trim()) c.procedure = procedure.trim();
    if (careSetting) c.care_setting = careSetting;
    if (daysNum !== undefined && !daysBad) c.days_since_surgery = daysNum;
    if (diabetic) c.is_diabetic = diabetic === "yes";
    return c;
  };

  const run = async () => {
    if (!q || busy || q.length > MAX_QUESTION || daysBad) return;
    const ctx = context();
    if (!live) {
      if (!settings) return;
      setResult({ question: q, context: ctx, r: demoTry(q, items, docs, settings, ctx) });
      return;
    }
    setBusy(true);
    try {
      setResult({ question: q, context: ctx, r: await api.tryKnowledge(q, ctx) });
    } catch (e) {
      toast.error(errorText(e, "Could not try the question."));
    } finally {
      setBusy(false);
    }
  };

  const r = result?.r;
  const cats = settings?.available_categories.length ? settings.available_categories : undefined;
  const cls = r?.classification ? classificationSummary(r.classification, cats, settings?.locked_categories) : null;
  const ctxText = result ? contextText(result.context) : "";
  const inputCls = "h-9 w-full rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40";

  return (
    <Panel className="space-y-3 px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
          <FlaskConical className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Try it</h3>
          <p className="text-[12px] text-ink-muted">
            Type a question as a patient would ask it and see what the agent would do, whatever the mode. Nothing is recorded on a patient.
            {live ? "" : " Sample data: a simple keyword match stands in for the real agent."}
          </p>
        </div>
      </div>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="kb-try" className="sr-only">Patient question</label>
          <input
            id="kb-try"
            value={question}
            maxLength={MAX_QUESTION}
            placeholder="e.g. Can I carry the laundry basket upstairs?"
            onChange={(e) => setQuestion(e.target.value)}
            className={cn(inputCls, "min-w-0 flex-1 basis-60")}
          />
          <button
            type="submit"
            disabled={!q || busy || daysBad || (!live && !settings)}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" /> {busy ? "Asking…" : "Ask"}
          </button>
        </div>
        <details className="rounded-2xl bg-mist/40 px-4 py-2.5 ring-1 ring-inset ring-black/[0.05]">
          <summary className="cursor-pointer text-[13px] font-semibold text-ink">
            Patient context <span className="font-normal text-ink-muted">(optional: which guidelines apply)</span>
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <label htmlFor="kb-try-proc" className="text-[12px] font-semibold text-ink">Procedure</label>
              <input id="kb-try-proc" value={procedure} maxLength={MAX_PROCEDURE} placeholder="Knee arthroscopy"
                onChange={(e) => setProcedure(e.target.value)} className={inputCls} />
            </div>
            <div className="space-y-1">
              <label htmlFor="kb-try-care" className="text-[12px] font-semibold text-ink">Care setting</label>
              <select id="kb-try-care" value={careSetting} onChange={(e) => setCareSetting(e.target.value as "" | CareSetting)}
                className={cn(inputCls, "px-2")}>
                <option value="">Not given</option>
                {CARE_SETTINGS.map((c) => <option key={c} value={c}>{CARE_SETTING_LABEL[c]}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label htmlFor="kb-try-days" className="text-[12px] font-semibold text-ink">Days since surgery</label>
              <input id="kb-try-days" inputMode="numeric" value={days} placeholder="e.g. 5"
                onChange={(e) => setDays(e.target.value.replace(/[^0-9]/g, ""))} className={inputCls} aria-invalid={daysBad} />
            </div>
            <div className="space-y-1">
              <label htmlFor="kb-try-diab" className="text-[12px] font-semibold text-ink">Diabetic</label>
              <select id="kb-try-diab" value={diabetic} onChange={(e) => setDiabetic(e.target.value as "" | "yes" | "no")}
                className={cn(inputCls, "px-2")}>
                <option value="">Not given</option>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </div>
          </div>
          {daysBad ? <p className="mt-2 text-[12px] text-brand-red-dark">Days since surgery must be a whole number up to 3650.</p> : null}
          <p className="mt-2 text-[12px] text-ink-muted">Never a name, date of birth, phone number or MRN: the agent is not given them on a call either.</p>
        </details>
      </form>

      {r ? (
        <div
          aria-live="polite"
          className={cn("space-y-3 rounded-2xl p-4 ring-1 ring-inset",
            r.outcome === "answered" ? "bg-brand/[0.06] ring-brand/25" : "bg-brand-yellow/15 ring-brand-yellow/40")}
        >
          <div>
            <p className="text-[12px] text-ink-muted">
              &ldquo;{result!.question}&rdquo;{ctxText ? <> · {ctxText}</> : null}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {r.outcome === "answered"
                ? <Pill tone="good" icon={BookOpenCheck}>Answered</Pill>
                : <Pill tone="routine">Deferred to a nurse</Pill>}
              <span className="text-[13px] text-ink">
                {reasonText(r.reason, {
                  category_label: cls?.category,
                  support_score: r.support_score,
                  min_support: settings?.min_support,
                })}
              </span>
            </div>
            {r.reason ? <p className="mt-1 text-[11px] text-ink-muted">Reason code: <code>{r.reason}</code></p> : null}
          </div>

          {cls ? (
            <div className="rounded-xl bg-white/80 px-3 py-2.5 ring-1 ring-inset ring-black/[0.05]">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">Classification</p>
              <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">
                <div><dt className="inline text-ink-muted">Topic </dt><dd className="inline font-semibold text-ink">{cls.category}</dd></div>
                <div><dt className="inline text-ink-muted">AI classifier </dt><dd className="inline text-ink">{cls.llm}</dd></div>
                <div><dt className="inline text-ink-muted">Keyword rules </dt><dd className="inline text-ink">{cls.keywords}</dd></div>
                <div>
                  <dt className="inline text-ink-muted">Red flag </dt>
                  <dd className={cn("inline", cls.redFlag ? "font-semibold text-brand-red-dark" : "text-ink")}>{cls.redFlag ? "Yes" : "No"}</dd>
                </div>
              </dl>
              {cls.veto ? (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-brand-red/10 px-3 py-1 text-[12px] font-semibold text-brand-red-dark">
                  <ShieldAlert className="h-3.5 w-3.5" /> {cls.veto}
                </p>
              ) : null}
            </div>
          ) : null}

          {r.answer ? (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                {r.outcome === "answered" ? "The patient would hear" : "The draft answer (withheld: not said to the patient)"}
              </p>
              <p className={cn("mt-0.5 text-[14px] leading-relaxed", r.outcome === "answered" ? "text-ink" : "text-ink-soft")}>
                &ldquo;{r.answer}&rdquo;
              </p>
            </div>
          ) : null}

          <SourceChips sources={r.sources} />

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <SupportScore value={r.support_score} min={settings?.min_support} />
            {typeof r.latency_ms === "number" ? (
              <span className="inline-flex items-center gap-1 text-[12px] tabular-nums text-ink-soft">
                <Timer className="h-3.5 w-3.5" /> {(r.latency_ms / 1000).toFixed(1)} s
                {r.latency_ms > 4000 ? <span className="font-semibold text-brand-coral-dark"> (over the 4 s live-call budget)</span> : null}
              </span>
            ) : null}
          </div>

          {r.note ? (
            <p className="rounded-xl bg-white/80 px-3 py-2 text-[12px] text-ink-soft ring-1 ring-inset ring-black/[0.05]">{r.note}</p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}

function contextText(c: KnowledgeTryContext): string {
  const parts: string[] = [];
  if (c.procedure) parts.push(c.procedure);
  if (c.care_setting) parts.push(CARE_SETTING_LABEL[c.care_setting]);
  if (typeof c.days_since_surgery === "number") parts.push(`day ${c.days_since_surgery}`);
  if (c.is_diabetic !== undefined) parts.push(c.is_diabetic ? "diabetic" : "not diabetic");
  return parts.join(", ");
}

// ---------------------------------------------------------------------------
// Q&A list
// ---------------------------------------------------------------------------

type Editing = { mode: "new" } | { mode: "edit"; item: KnowledgeItem } | null;

function QaList({
  live,
  items,
  error,
  onRetry,
  settings,
  timeZone,
  sentBack,
  onCreated,
  onChanged,
  onDeleted,
}: {
  live: boolean;
  items: KnowledgeItem[] | null;
  error: string | null;
  onRetry: () => void;
  settings: KnowledgeSettings | null;
  timeZone?: string;
  sentBack: Set<string>;
  onCreated: (it: KnowledgeItem) => void;
  onChanged: (it: KnowledgeItem, previous?: KnowledgeItem) => void;
  onDeleted: (id: string) => void;
}) {
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Editing>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const shown = useMemo(() => (items ? sortItems(filterItems(items, status, search)) : []), [items, status, search]);
  const counts = useMemo(() => statusCounts(items ?? []), [items]);
  const reapprove = (items ?? []).filter((i) => needsReapproval(i) || (i.status === "draft" && sentBack.has(i.item_id)));

  const act = async (item: KnowledgeItem, kind: "approve" | "retire" | "delete") => {
    if (kind === "approve" && !window.confirm(
      `Approve this answer?\n\nThe agent may answer patients from it, in this wording or close to it:\n\n"${item.answer}"`)) return;
    if (kind === "retire" && !window.confirm(
      `Retire "${item.question}"? The agent stops using it at once; you can approve it again later.`)) return;
    if (kind === "delete" && !window.confirm(`Delete "${item.question}"? This cannot be undone.`)) return;

    if (!live) {
      const at = new Date().toISOString();
      if (kind === "delete") onDeleted(item.item_id);
      else if (kind === "approve") onChanged({ ...item, status: "approved", approved_at: at, approved_by: "you (sample)", updated_at: at }, item);
      else onChanged({ ...item, status: "retired", updated_at: at }, item);
      toast.info(DEMO_TOAST);
      return;
    }
    setBusyId(item.item_id);
    try {
      if (kind === "delete") {
        await api.deleteKnowledgeItem(item.item_id);
        onDeleted(item.item_id);
        toast.success("Entry deleted");
      } else if (kind === "approve") {
        onChanged(await api.approveKnowledgeItem(item.item_id), item);
        toast.success("Approved. The agent can use it on new calls.");
      } else {
        onChanged(await api.retireKnowledgeItem(item.item_id), item);
        toast.success("Retired. The agent no longer uses it.");
      }
    } catch (e) {
      toast.error(errorText(e, "Could not change the entry."));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <Library className="h-4 w-4 text-brand" />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Example questions and answers</h3>
          <p className="text-[12px] text-ink-muted">
            Typical questions and the clinic&rsquo;s preferred answer. A patient&rsquo;s question need not match word for word: the agent
            goes by meaning. Only approved entries are used; editing an approved entry sends it back to draft.
          </p>
        </div>
        <button
          type="button"
          disabled={!settings || items === null}
          onClick={() => setEditing({ mode: "new" })}
          className="inline-flex items-center gap-1.5 rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> New example
        </button>
      </div>

      {error ? (
        <div className="flex items-center gap-3 border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark" role="alert">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
          <button onClick={onRetry} className="ml-auto rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-black/[0.08]">
            Try again
          </button>
        </div>
      ) : null}

      {reapprove.length ? (
        <div className="flex items-start gap-2 border-b border-black/[0.05] bg-brand-orange/15 px-5 py-2.5 text-[13px] text-ink" role="status">
          <RotateCcw className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <strong>Back to draft: needs re-approval.</strong>{" "}
            {reapprove.length === 1
              ? `"${reapprove[0].question}" was edited after approval. The agent won't use it until it is approved again.`
              : `${reapprove.length} entries were edited after approval. The agent won't use them until they are approved again.`}
          </span>
        </div>
      ) : null}

      <div className="space-y-3 px-5 py-3.5">
        <FilterChips<StatusFilter>
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "All", count: counts.all },
            { value: "draft", label: "Drafts", count: counts.draft, tone: "routine" },
            { value: "approved", label: "Approved", count: counts.approved, tone: "good" },
            { value: "retired", label: "Retired", count: counts.retired, tone: "muted" },
          ]}
        />
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" />
          <label htmlFor="kb-search" className="sr-only">Search the examples</label>
          <input
            id="kb-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search questions and answers"
            className="h-9 w-full rounded-xl border border-black/[0.08] bg-white pl-9 pr-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
          />
        </div>
      </div>

      {items === null ? (
        <p className="px-5 pb-5 text-[13px] text-ink-muted">{error ? "Could not load the examples." : "Loading…"}</p>
      ) : shown.length === 0 ? (
        <EmptyState
          title={items.length ? "No entries match" : "No examples yet"}
          hint={items.length ? "Try another status or search." : "Add the clinic's answers to common questions, then approve them."}
        />
      ) : (
        <ul className="divide-y divide-black/[0.05] border-t border-black/[0.05]">
          {shown.map((it) => {
            const back = it.status === "draft" && (needsReapproval(it) || sentBack.has(it.item_id));
            const busy = busyId === it.item_id;
            return (
              <li key={it.item_id} className="px-5 py-3.5">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[14px] font-semibold leading-snug text-ink">{it.question}</p>
                    {it.alt_questions.length ? (
                      <p className="mt-0.5 truncate text-[12px] text-ink-muted" title={it.alt_questions.join("\n")}>
                        Also: {it.alt_questions.join(" · ")}
                      </p>
                    ) : null}
                    <p className="mt-1 line-clamp-2 text-[13px] text-ink-soft">{it.answer}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Pill tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status]}</Pill>
                      {back ? <Pill tone="warn" icon={RotateCcw}>Needs re-approval</Pill> : null}
                      <Pill tone="muted">{it.category_label || categoryLabelOf(it.category, settings?.available_categories)}</Pill>
                      <span className="text-[12px] tabular-nums text-ink-muted">v{it.version}</span>
                      <span className="text-[12px] text-ink-muted">
                        {it.approved_by
                          ? `· ${it.status === "approved" ? "Approved" : "Last approved"} by ${it.approved_by}, ${fmtDateTime(it.approved_at, { month: "short", day: "numeric", timeZone })}`
                          : "· Never approved"}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    <RowButton icon={Pencil} onClick={() => setEditing({ mode: "edit", item: it })} disabled={busy || !settings}>Edit</RowButton>
                    {canApprove(it) ? (
                      <RowButton icon={CheckCircle2} tone="good" onClick={() => act(it, "approve")} disabled={busy}>Approve</RowButton>
                    ) : null}
                    {canRetire(it) ? (
                      <RowButton icon={Archive} onClick={() => act(it, "retire")} disabled={busy}>Retire</RowButton>
                    ) : null}
                    {canDelete(it) ? (
                      <RowButton icon={Trash2} tone="bad" onClick={() => act(it, "delete")} disabled={busy}>Delete</RowButton>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {settings ? (
        <ItemDialog
          live={live}
          editing={editing}
          settings={settings}
          onClose={() => setEditing(null)}
          onSaved={(it, previous) => {
            if (previous) {
              onChanged(it, previous);
              if (previous.status === "approved" && it.status === "draft") {
                toast.warning(`Back to draft (v${it.version}): approve it again before the agent uses it.`);
              }
            } else {
              onCreated(it);
            }
          }}
        />
      ) : null}
    </Panel>
  );
}

function RowButton({
  icon: Icon,
  tone,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: ComponentType<{ className?: string }>; tone?: "good" | "bad" }) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-white px-3 py-1.5 text-[12px] font-semibold ring-1 ring-black/[0.08] disabled:opacity-50",
        tone === "good" ? "text-brand-dark hover:bg-brand/10" : tone === "bad" ? "text-brand-red-dark hover:bg-brand-red/10" : "text-ink-soft hover:text-ink",
      )}
    >
      <Icon className="h-3.5 w-3.5" /> {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Guidelines
// ---------------------------------------------------------------------------

type DocEditing = { mode: "new" } | { mode: "edit"; doc: KnowledgeDoc } | null;

function GuidelinesList({
  live,
  docs,
  error,
  onRetry,
  timeZone,
  sentBack,
  onCreated,
  onChanged,
  onDeleted,
}: {
  live: boolean;
  docs: KnowledgeDoc[] | null;
  error: string | null;
  onRetry: () => void;
  timeZone?: string;
  sentBack: Set<string>;
  onCreated: (d: KnowledgeDoc) => void;
  onChanged: (d: KnowledgeDoc, previous?: KnowledgeDoc) => void;
  onDeleted: (id: string) => void;
}) {
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<DocEditing>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const all = docs ?? [];
  const shown = useMemo(() => sortDocs(filterDocs(all, status, search)), [all, status, search]);
  const counts = useMemo(() => statusCounts(all), [all]);
  const used = approvedChars(all);
  const reapprove = all.filter((d) => needsReapproval(d) || (d.status === "draft" && sentBack.has(d.doc_id)));
  const full = all.length >= MAX_DOCS;

  const act = async (doc: KnowledgeDoc, kind: "approve" | "retire" | "delete") => {
    if (kind === "approve") {
      if (approvalOverBudget(doc, all)) {
        toast.error(
          `Approving "${doc.title}" would pass the ${APPROVED_CHARS_BUDGET.toLocaleString("en-US")}-character budget for approved guidelines. Retire or shorten another guideline first.`,
        );
        return;
      }
      if (!window.confirm(
        `Approve "${doc.title}"?\n\nThe agent may answer patients from this text (${doc.chars.toLocaleString("en-US")} characters). Applies to: ${appliesToText(doc)}.`,
      )) return;
    }
    if (kind === "retire" && !window.confirm(
      `Retire "${doc.title}"? The agent stops using it at once; you can approve it again later.`)) return;
    if (kind === "delete" && !window.confirm(`Delete "${doc.title}"? This cannot be undone.`)) return;

    if (!live) {
      const at = new Date().toISOString();
      if (kind === "delete") onDeleted(doc.doc_id);
      else if (kind === "approve") onChanged({ ...doc, status: "approved", approved_at: at, approved_by: "you (sample)", updated_at: at }, doc);
      else onChanged({ ...doc, status: "retired", updated_at: at }, doc);
      toast.info(DEMO_TOAST);
      return;
    }
    setBusyId(doc.doc_id);
    try {
      if (kind === "delete") {
        await api.deleteKnowledgeDoc(doc.doc_id);
        onDeleted(doc.doc_id);
        toast.success("Guideline deleted");
      } else if (kind === "approve") {
        onChanged(await api.approveKnowledgeDoc(doc.doc_id), doc);
        toast.success("Approved. The agent can use it on new calls.");
      } else {
        onChanged(await api.retireKnowledgeDoc(doc.doc_id), doc);
        toast.success("Retired. The agent no longer uses it.");
      }
    } catch (e) {
      toast.error(errorText(e, "Could not change the guideline."));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
        <FileText className="h-4 w-4 text-brand" />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Guidelines</h3>
          <p className="text-[12px] text-ink-muted">
            Clinic instructions in your own words, such as discharge advice for a procedure. The agent answers only from approved
            text that applies to the patient. Editing an approved guideline sends it back to draft.
          </p>
        </div>
        <button
          type="button"
          disabled={docs === null || full}
          title={full ? `At most ${MAX_DOCS} guidelines` : undefined}
          onClick={() => setEditing({ mode: "new" })}
          className="inline-flex items-center gap-1.5 rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> New guideline
        </button>
      </div>

      {error ? (
        <div className="flex items-center gap-3 border-b border-black/[0.05] bg-brand-red/10 px-5 py-2.5 text-[13px] text-brand-red-dark" role="alert">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
          <button onClick={onRetry} className="ml-auto rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-black/[0.08]">
            Try again
          </button>
        </div>
      ) : null}

      {reapprove.length ? (
        <div className="flex items-start gap-2 border-b border-black/[0.05] bg-brand-orange/15 px-5 py-2.5 text-[13px] text-ink" role="status">
          <RotateCcw className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <strong>Back to draft: needs re-approval.</strong>{" "}
            {reapprove.length === 1
              ? `"${reapprove[0].title}" was edited after approval. The agent won't use it until it is approved again.`
              : `${reapprove.length} guidelines were edited after approval. The agent won't use them until they are approved again.`}
          </span>
        </div>
      ) : null}

      <div className="space-y-3 px-5 py-3.5">
        <div>
          <div className="flex items-baseline justify-between text-[12px]">
            <span className="font-semibold text-ink">Approved text</span>
            <span className={cn("tabular-nums", used > APPROVED_CHARS_BUDGET ? "font-semibold text-brand-red-dark" : "text-ink-soft")}>
              {used.toLocaleString("en-US")} / {APPROVED_CHARS_BUDGET.toLocaleString("en-US")} characters
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-mist" role="progressbar" aria-label="Approved characters"
            aria-valuemin={0} aria-valuemax={APPROVED_CHARS_BUDGET} aria-valuenow={used}>
            <div className={cn("h-full rounded-full", used / APPROVED_CHARS_BUDGET > 0.9 ? "bg-brand-coral" : "bg-brand")}
              style={{ width: `${Math.min(100, (used / APPROVED_CHARS_BUDGET) * 100)}%` }} />
          </div>
        </div>
        <FilterChips<StatusFilter>
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "All", count: counts.all },
            { value: "draft", label: "Drafts", count: counts.draft, tone: "routine" },
            { value: "approved", label: "Approved", count: counts.approved, tone: "good" },
            { value: "retired", label: "Retired", count: counts.retired, tone: "muted" },
          ]}
        />
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" />
          <label htmlFor="kb-doc-search" className="sr-only">Search the guidelines</label>
          <input
            id="kb-doc-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search titles, text and procedures"
            className="h-9 w-full rounded-xl border border-black/[0.08] bg-white pl-9 pr-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
          />
        </div>
      </div>

      {docs === null ? (
        <p className="px-5 pb-5 text-[13px] text-ink-muted">{error ? "Could not load the guidelines." : "Loading…"}</p>
      ) : shown.length === 0 ? (
        <EmptyState
          title={docs.length ? "No guidelines match" : "No guidelines yet"}
          hint={docs.length ? "Try another status or search." : "Paste or load the clinic's instructions, then approve them."}
        />
      ) : (
        <ul className="divide-y divide-black/[0.05] border-t border-black/[0.05]">
          {shown.map((d) => {
            const back = d.status === "draft" && (needsReapproval(d) || sentBack.has(d.doc_id));
            const busy = busyId === d.doc_id;
            return (
              <li key={d.doc_id} className="px-5 py-3.5">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[14px] font-semibold leading-snug text-ink">{d.title}</p>
                    <p className="mt-1 line-clamp-2 text-[13px] text-ink-soft">{d.body}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Pill tone={STATUS_TONE[d.status]}>{STATUS_LABEL[d.status]}</Pill>
                      {back ? <Pill tone="warn" icon={RotateCcw}>Needs re-approval</Pill> : null}
                      <span className="text-[12px] tabular-nums text-ink-muted">v{d.version}</span>
                      <span className="text-[12px] tabular-nums text-ink-muted">· {d.chars.toLocaleString("en-US")} chars</span>
                      <span className="text-[12px] text-ink-muted">
                        {d.approved_by
                          ? `· ${d.status === "approved" ? "Approved" : "Last approved"} by ${d.approved_by}, ${fmtDateTime(d.approved_at, { month: "short", day: "numeric", timeZone })}`
                          : "· Never approved"}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <span className="text-[12px] text-ink-muted">Applies to</span>
                      {d.procedures.length || d.care_settings.length ? (
                        <>
                          {d.procedures.map((p) => <Pill key={`p-${p}`} tone="info">{p}</Pill>)}
                          {d.care_settings.map((c) => <Pill key={`c-${c}`} tone="teal">{CARE_SETTING_LABEL[c] ?? c}</Pill>)}
                        </>
                      ) : (
                        <Pill tone="muted">Every patient</Pill>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    <RowButton icon={Pencil} onClick={() => setEditing({ mode: "edit", doc: d })} disabled={busy}>Edit</RowButton>
                    {canApprove(d) ? (
                      <RowButton icon={CheckCircle2} tone="good" onClick={() => act(d, "approve")} disabled={busy}>Approve</RowButton>
                    ) : null}
                    {canRetire(d) ? (
                      <RowButton icon={Archive} onClick={() => act(d, "retire")} disabled={busy}>Retire</RowButton>
                    ) : null}
                    {canDelete(d) ? (
                      <RowButton icon={Trash2} tone="bad" onClick={() => act(d, "delete")} disabled={busy}>Delete</RowButton>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <DocDialog
        live={live}
        editing={editing}
        onClose={() => setEditing(null)}
        onSaved={(d, previous) => {
          if (previous) {
            onChanged(d, previous);
            if (previous.status === "approved" && d.status === "draft") {
              toast.warning(`Back to draft (v${d.version}): approve it again before the agent uses it.`);
            }
          } else {
            onCreated(d);
          }
        }}
      />
    </Panel>
  );
}

function DocDialog({
  live,
  editing,
  onClose,
  onSaved,
}: {
  live: boolean;
  editing: DocEditing;
  onClose: () => void;
  onSaved: (d: KnowledgeDoc, previous?: KnowledgeDoc) => void;
}) {
  const doc = editing?.mode === "edit" ? editing.doc : null;
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [procedures, setProcedures] = useState<string[]>([]);
  const [procText, setProcText] = useState("");
  const [careSettings, setCareSettings] = useState<CareSetting[]>([]);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) return;
    setTitle(doc?.title ?? "");
    setBody(doc?.body ?? "");
    setProcedures(doc?.procedures ?? []);
    setProcText("");
    setCareSettings(doc?.care_settings ?? []);
    setServerError(null);
    setFileError(null);
    // Reset only when the dialog opens on a guideline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  // A name still being typed counts, so "Save" never drops it.
  const allProcedures = addProcedures(procedures, procText);
  const input: KnowledgeDocInput = {
    title: title.trim(),
    body,
    procedures: allProcedures,
    care_settings: CARE_SETTINGS.filter((c) => careSettings.includes(c)),
  };
  const problem = docDraftError(input);
  const unchanged = !!doc &&
    input.title === doc.title &&
    input.body === doc.body &&
    JSON.stringify(input.procedures) === JSON.stringify(doc.procedures) &&
    JSON.stringify(input.care_settings) === JSON.stringify(CARE_SETTINGS.filter((c) => doc.care_settings.includes(c)));

  const commitProcedures = () => {
    if (!procText.trim()) return;
    setProcedures(addProcedures(procedures, procText));
    setProcText("");
  };

  const loadFile = (file: File | undefined) => {
    setFileError(null);
    if (!file) return;
    const pre = guidelineFileError(file.name, file.size);
    if (pre) {
      setFileError(pre);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setFileError("Could not read the file.");
    reader.onload = () => {
      const text = normaliseFileText(typeof reader.result === "string" ? reader.result : "");
      const err = guidelineTextError(text);
      if (err) {
        setFileError(err);
        return;
      }
      if (body.trim() && !window.confirm("Replace the text in the editor with the file's text?")) return;
      setBody(text);
      if (!title.trim()) setTitle(titleFromFileName(file.name));
      toast.info(`Loaded ${file.name} (${text.length.toLocaleString("en-US")} characters). Check it before saving.`);
    };
    reader.readAsText(file);
  };

  const save = async () => {
    if (problem || busy || unchanged) return;
    if (!live) {
      onSaved(doc ? demoEditDoc(doc, input) : demoCreateDoc(input), doc ?? undefined);
      toast.info(DEMO_TOAST);
      onClose();
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const saved = doc
        ? await api.updateKnowledgeDoc(doc.doc_id, input)
        : await api.createKnowledgeDoc(input);
      onSaved(saved, doc ?? undefined);
      if (!(doc?.status === "approved" && saved.status === "draft")) toast.success(doc ? "Guideline saved" : "Guideline added as a draft");
      onClose();
    } catch (e) {
      // 422: too long, or a PHI-looking pattern. Keep the dialog open with the reason.
      setServerError(errorText(e, "Could not save the guideline."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!editing} onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display">{doc ? "Edit guideline" : "New guideline"}</DialogTitle>
          <DialogDescription>
            Clinic material only: never a patient&rsquo;s name, number or details.
            {doc ? ` Version ${doc.version}, ${STATUS_LABEL[doc.status].toLowerCase()}.` : " New guidelines start as drafts."}
          </DialogDescription>
        </DialogHeader>

        {doc?.status === "approved" ? (
          <p className="flex items-start gap-2 rounded-xl bg-brand-orange/15 px-3 py-2 text-[12px] text-ink">
            <RotateCcw className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Saving sends this guideline back to draft as version {doc.version + 1}. The agent stops using it until it is approved again.
          </p>
        ) : null}

        <div className="space-y-4">
          <Field label="Title" htmlFor="kb-doc-title" count={`${title.trim().length} / ${MAX_DOC_TITLE}`} over={title.trim().length > MAX_DOC_TITLE}>
            <input
              id="kb-doc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Lifting after outpatient knee arthroscopy"
              className="h-9 w-full rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            />
          </Field>

          <Field
            label="Text"
            htmlFor="kb-doc-body"
            hint="Write it as you would explain it to a patient. The agent answers in its own short words, only from this text."
            count={`${body.length.toLocaleString("en-US")} / ${MAX_DOC_BODY.toLocaleString("en-US")}`}
            over={body.length > MAX_DOC_BODY}
          >
            <textarea
              id="kb-doc-body"
              value={body}
              rows={10}
              onChange={(e) => setBody(e.target.value)}
              placeholder="For two weeks after outpatient knee arthroscopy, lift nothing heavier than 10 pounds…"
              className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            />
          </Field>
          <div className="-mt-2">
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept={GUIDELINE_FILE_ACCEPT}
                className="sr-only"
                tabIndex={-1}
                aria-hidden
                onChange={(e) => { loadFile(e.target.files?.[0]); e.target.value = ""; }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-[12px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink"
              >
                <Upload className="h-3.5 w-3.5" /> Load from file
              </button>
              <span className="text-[12px] text-ink-muted">.txt or .md, read in your browser; nothing is uploaded until you save.</span>
            </div>
            {fileError ? <p className="mt-1 text-[12px] text-brand-red-dark" role="alert">{fileError}</p> : null}
          </div>

          <Field
            label="Applies to procedures"
            htmlFor="kb-doc-proc"
            hint="Leave empty for every procedure. A name matches a patient's procedure when either contains the other, so “knee” matches “Knee arthroscopy”."
            count={`${allProcedures.length} / ${MAX_PROCEDURES}`}
            over={allProcedures.length > MAX_PROCEDURES}
          >
            <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-xl border border-black/[0.08] bg-white px-2 py-1.5 focus-within:ring-2 focus-within:ring-brand/40">
              {procedures.map((p) => (
                <span key={p} className="inline-flex items-center gap-1 rounded-full bg-brand-blue/10 py-0.5 pl-2.5 pr-1 text-[12px] font-semibold text-brand-blue-dark">
                  {p}
                  <button
                    type="button"
                    aria-label={`Remove ${p}`}
                    onClick={() => setProcedures(procedures.filter((x) => x !== p))}
                    className="grid h-4 w-4 place-items-center rounded-full hover:bg-brand-blue/20"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              <input
                id="kb-doc-proc"
                value={procText}
                onChange={(e) => setProcText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    commitProcedures();
                  } else if (e.key === "Backspace" && !procText && procedures.length) {
                    setProcedures(procedures.slice(0, -1));
                  }
                }}
                onBlur={commitProcedures}
                placeholder={procedures.length ? "Add another" : "e.g. knee arthroscopy, then Enter"}
                className="h-6 min-w-[10rem] flex-1 bg-transparent px-1 text-[14px] text-ink outline-none"
              />
            </div>
          </Field>

          <fieldset className="space-y-1">
            <legend className="text-[13px] font-semibold text-ink">Applies to care settings</legend>
            <div className="flex flex-wrap gap-3">
              {CARE_SETTINGS.map((c) => (
                <label key={c} className="inline-flex items-center gap-2 rounded-full bg-white px-3.5 py-1.5 text-[13px] font-semibold text-ink ring-1 ring-black/[0.08]">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-brand"
                    checked={careSettings.includes(c)}
                    onChange={(e) => setCareSettings(e.target.checked ? [...careSettings, c] : careSettings.filter((x) => x !== c))}
                  />
                  {CARE_SETTING_LABEL[c]}
                </label>
              ))}
            </div>
            <p className="text-[12px] text-ink-muted">None ticked: every patient, inpatient or outpatient.</p>
          </fieldset>

          {serverError ? (
            <p className="rounded-xl bg-brand-red/10 px-3 py-2 text-[13px] text-brand-red-dark" role="alert">{serverError}</p>
          ) : problem && (title || body) ? (
            <p className="text-[12px] text-brand-red-dark">{problem}</p>
          ) : null}
          {!live ? <p className="text-[12px] text-ink-muted">Sample data: saving changes this tab only. Nothing is sent.</p> : null}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" onClick={save} disabled={!!problem || busy || unchanged}>
            {busy ? "Saving…" : doc ? (doc.status === "approved" ? "Save as draft" : "Save") : "Add as draft"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// New / edit dialog
// ---------------------------------------------------------------------------

function ItemDialog({
  live,
  editing,
  settings,
  onClose,
  onSaved,
}: {
  live: boolean;
  editing: Editing;
  settings: KnowledgeSettings;
  onClose: () => void;
  onSaved: (it: KnowledgeItem, previous?: KnowledgeItem) => void;
}) {
  const categories = editableCategories(settings);
  const item = editing?.mode === "edit" ? editing.item : null;
  const [question, setQuestion] = useState("");
  const [alt, setAlt] = useState("");
  const [answer, setAnswer] = useState("");
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) return;
    setQuestion(item?.question ?? "");
    setAlt(item?.alt_questions.join("\n") ?? "");
    setAnswer(item?.answer ?? "");
    setCategory(item?.category ?? categories[0]?.key ?? "");
    setServerError(null);
    // Reset only when the dialog opens on an entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const input: KnowledgeItemInput = {
    question: question.trim(),
    answer: answer.trim(),
    category,
    alt_questions: parseAltQuestions(alt, question),
  };
  const keys = categories.map((c) => c.key);
  // An entry already filed under a category no longer offered can be kept as is.
  if (item && !keys.includes(item.category)) keys.push(item.category);
  const problem = itemDraftError(input, keys);
  const unchanged = !!item &&
    input.question === item.question &&
    input.answer === item.answer &&
    input.category === item.category &&
    JSON.stringify(input.alt_questions) === JSON.stringify(item.alt_questions);

  const save = async () => {
    if (problem || busy || unchanged) return;
    if (!live) {
      onSaved(item ? demoEdit(item, input) : demoCreate(input), item ?? undefined);
      toast.info(DEMO_TOAST);
      onClose();
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const saved = item
        ? await api.updateKnowledgeItem(item.item_id, input)
        : await api.createKnowledgeItem(input);
      onSaved(saved, item ?? undefined);
      if (!(item?.status === "approved" && saved.status === "draft")) toast.success(item ? "Entry saved" : "Entry added as a draft");
      onClose();
    } catch (e) {
      // 422: too long, or a PHI-looking pattern. Keep the dialog open with the reason.
      setServerError(errorText(e, "Could not save the entry."));
    } finally {
      setBusy(false);
    }
  };

  const altCount = parseAltQuestions(alt, question).length;

  return (
    <Dialog open={!!editing} onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display">{item ? "Edit example" : "New example question and answer"}</DialogTitle>
          <DialogDescription>
            Clinic material only: never a patient&rsquo;s name, number or details.
            {item ? ` Version ${item.version}, ${STATUS_LABEL[item.status].toLowerCase()}.` : " New entries start as drafts."}
          </DialogDescription>
        </DialogHeader>

        {item?.status === "approved" ? (
          <p className="flex items-start gap-2 rounded-xl bg-brand-orange/15 px-3 py-2 text-[12px] text-ink">
            <RotateCcw className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Saving sends this entry back to draft as version {item.version + 1}. The agent stops using it until it is approved again.
          </p>
        ) : null}

        <div className="space-y-4">
          <Field label="Patient's question" htmlFor="kb-q" count={`${question.trim().length} / ${MAX_QUESTION}`} over={question.trim().length > MAX_QUESTION}>
            <input
              id="kb-q"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="When can I shower?"
              className="h-9 w-full rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            />
          </Field>

          <Field
            label="Other ways patients ask it"
            hint="One per line, optional. Patients need not use these exact words; a few wordings help the agent recognise the question."
            htmlFor="kb-alt"
            count={`${altCount} / ${MAX_ALT_QUESTIONS}`}
            over={altCount > MAX_ALT_QUESTIONS}
          >
            <textarea
              id="kb-alt"
              value={alt}
              rows={3}
              onChange={(e) => setAlt(e.target.value)}
              placeholder={"Can I take a shower?\nIs it OK to get my bandage wet?"}
              className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            />
          </Field>

          <Field
            label="Answer"
            hint="The clinic's preferred wording. Write it as you would say it."
            htmlFor="kb-a"
            count={`${answer.trim().length} / ${MAX_ANSWER}`}
            over={answer.trim().length > MAX_ANSWER}
          >
            <textarea
              id="kb-a"
              value={answer}
              rows={5}
              onChange={(e) => setAnswer(e.target.value)}
              className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            />
          </Field>

          <Field label="Category" htmlFor="kb-cat" hint="For filtering and display. Medication and symptom questions are never answered, so they are not offered.">
            <select
              id="kb-cat"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="h-9 w-full rounded-xl border border-black/[0.08] bg-white px-2 text-[14px] text-ink"
            >
              {categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              {item && !categories.some((c) => c.key === item.category) ? (
                <option value={item.category}>{item.category_label || item.category}</option>
              ) : null}
            </select>
          </Field>

          {serverError ? (
            <p className="rounded-xl bg-brand-red/10 px-3 py-2 text-[13px] text-brand-red-dark" role="alert">{serverError}</p>
          ) : problem && (question || answer) ? (
            <p className="text-[12px] text-brand-red-dark">{problem}</p>
          ) : null}
          {!live ? <p className="text-[12px] text-ink-muted">Sample data: saving changes this tab only. Nothing is sent.</p> : null}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" onClick={save} disabled={!!problem || busy || unchanged}>
            {busy ? "Saving…" : item ? (item.status === "approved" ? "Save as draft" : "Save") : "Add as draft"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  htmlFor,
  count,
  over,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor: string;
  count?: string;
  over?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[13px] font-semibold text-ink">{label}</label>
        {count ? <span className={cn("text-[12px] tabular-nums", over ? "font-semibold text-brand-red-dark" : "text-ink-muted")}>{count}</span> : null}
      </div>
      {children}
      {hint ? <p className="text-[12px] text-ink-muted">{hint}</p> : null}
    </div>
  );
}
