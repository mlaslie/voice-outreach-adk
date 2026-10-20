"use client";

import { useCallback, useEffect, useMemo, useState, type ButtonHTMLAttributes, type ComponentType, type ReactNode } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Archive,
  BookOpenCheck,
  CheckCircle2,
  FlaskConical,
  Library,
  Lock,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import {
  KnowledgeItem,
  KnowledgeItemInput,
  KnowledgeSettings,
  KnowledgeStatus,
  KnowledgeTryResult,
  MAX_ALT_QUESTIONS,
  MAX_ANSWER,
  MAX_QUESTION,
  MIN_MATCH_RANGE,
  STATUS_LABEL,
  StatusFilter,
  canApprove,
  canDelete,
  canRetire,
  categoryLabelOf,
  clampMinMatch,
  demoCreate,
  demoEdit,
  demoItems,
  demoSettings,
  demoTry,
  editableCategories,
  filterItems,
  itemDraftError,
  needsReapproval,
  parseAltQuestions,
  settingsPatch,
  sortItems,
  statusCounts,
  toggleCategory,
} from "@/lib/knowledge";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { EmptyState, FilterChips, Panel, Pill, Tone } from "@/components/nurse/parts";
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
 * Admin → Knowledge base (docs/KNOWLEDGE-PLAN.md, step 2): the clinic's
 * approved Q&A the voice agent may read to a patient word for word, the
 * switches that govern it, and "Try it". Live: /api/admin/knowledge/* (admin
 * build only, audited). Sample data (?data=demo): fictional entries held in
 * this tab, a keyword "Try it", nothing sent.
 */

const DEMO_TOAST = "Sample data: nothing is sent.";

const STATUS_TONE: Record<KnowledgeStatus, Tone> = { draft: "routine", approved: "good", retired: "muted" };

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message : fallback;
}

export function KnowledgePanel({ live, timeZone }: { live: boolean; timeZone?: string }) {
  const [items, setItems] = useState<KnowledgeItem[] | null>(live ? null : demoItems());
  const [settings, setSettings] = useState<KnowledgeSettings | null>(live ? null : demoSettings());
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  /** Entries this session sent back to draft by editing them while approved. */
  const [sentBack, setSentBack] = useState<Set<string>>(new Set());

  const loadItems = useCallback(() => {
    setItemsError(null);
    api.getKnowledgeItems()
      .then((r) => setItems(Array.isArray(r.items) ? r.items : []))
      .catch((e) => setItemsError(errorText(e, "Could not load the knowledge base.")));
  }, []);
  const loadSettings = useCallback(() => {
    setSettingsError(null);
    api.getKnowledgeSettings()
      .then(setSettings)
      .catch((e) => setSettingsError(errorText(e, "Could not load the knowledge base settings.")));
  }, []);

  useEffect(() => {
    if (!live) return;
    loadItems();
    loadSettings();
  }, [live, loadItems, loadSettings]);

  const replace = (item: KnowledgeItem) =>
    setItems((xs) => (xs ? xs.map((x) => (x.item_id === item.item_id ? item : x)) : xs));

  const approvedCount = items?.filter((i) => i.status === "approved").length ?? 0;

  return (
    <>
      <SettingsCard
        live={live}
        settings={settings}
        error={settingsError}
        onRetry={loadSettings}
        onSaved={setSettings}
        approvedCount={items ? approvedCount : null}
      />
      <TryItCard live={live} items={items ?? []} settings={settings} />
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

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function SettingsCard({
  live,
  settings,
  error,
  onRetry,
  onSaved,
  approvedCount,
}: {
  live: boolean;
  settings: KnowledgeSettings | null;
  error: string | null;
  onRetry: () => void;
  onSaved: (s: KnowledgeSettings) => void;
  approvedCount: number | null;
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

  const save = async () => {
    if (!dirty || busy) return;
    if (!live) {
      onSaved(draft);
      toast.info(DEMO_TOAST);
      return;
    }
    setBusy(true);
    try {
      const next = await api.saveKnowledgeSettings(patch);
      onSaved(next);
      toast.success(next.enabled ? "Saved. The agent answers from approved Q&A on new calls." : "Saved. The agent answers no questions.");
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
            When a patient asks a question on a call, the agent may read the clinic&rsquo;s approved answer, word for word.
            Anything else goes to a nurse, as today.
          </p>
        </div>
      </div>

      <label className={cn("flex items-start gap-3 rounded-2xl px-4 py-3 ring-1 ring-inset",
        draft.enabled ? "bg-brand/[0.07] ring-brand/30" : "bg-mist/50 ring-black/[0.06]")}>
        <Switch checked={draft.enabled} onChange={(v) => set({ enabled: v })} disabled={busy} label="Answer patient questions from approved Q&A" />
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-semibold text-ink">Answer patient questions from approved Q&amp;A</span>
          <span className="block text-[12px] text-ink-muted">
            {draft.enabled
              ? "On: a question that matches an approved entry in an allowed category is answered; every other question goes to a nurse."
              : "Off: the agent answers nothing. It records each question for a nurse to call back, exactly as today."}
          </span>
          {draft.enabled && approvedCount === 0 ? (
            <span className="mt-1 block text-[12px] font-semibold text-brand-coral-dark">
              No entries are approved yet, so every question still goes to a nurse.
            </span>
          ) : null}
        </span>
      </label>

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
        <legend className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink-muted">Categories the agent may answer</legend>
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
          Medication and symptom questions always go to a nurse the same day, whatever is set here. Unclassified questions go to a nurse too.
        </p>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label htmlFor="kb-min-match" className="flex items-baseline justify-between text-[13px] font-semibold text-ink">
            Minimum match
            <span className="tabular-nums text-ink-soft">{Math.round(draft.min_match * 100)}%</span>
          </label>
          <input
            id="kb-min-match"
            type="range"
            min={MIN_MATCH_RANGE.min}
            max={MIN_MATCH_RANGE.max}
            step={MIN_MATCH_RANGE.step}
            value={draft.min_match}
            disabled={busy}
            onChange={(e) => set({ min_match: clampMinMatch(Number(e.target.value)) })}
            className="w-full accent-brand"
          />
          <p className="text-[12px] text-ink-muted">How closely a patient&rsquo;s question must match an entry before the agent answers. Higher defers more.</p>
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
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </Panel>
  );
}

function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={(e) => { e.preventDefault(); onChange(!checked); }}
      className={cn("relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-60",
        checked ? "bg-brand-deep" : "bg-ink-muted/40")}
    >
      <span className={cn("inline-block h-5 w-5 rounded-full bg-white shadow transition-transform", checked ? "translate-x-[22px]" : "translate-x-0.5")} />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Try it
// ---------------------------------------------------------------------------

function TryItCard({ live, items, settings }: { live: boolean; items: KnowledgeItem[]; settings: KnowledgeSettings | null }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<{ question: string; r: KnowledgeTryResult } | null>(null);
  const [busy, setBusy] = useState(false);
  const q = question.trim();

  const run = async () => {
    if (!q || busy || q.length > MAX_QUESTION) return;
    if (!live) {
      if (!settings) return;
      setResult({ question: q, r: demoTry(q, items, settings) });
      return;
    }
    setBusy(true);
    try {
      setResult({ question: q, r: await api.tryKnowledge(q) });
    } catch (e) {
      toast.error(errorText(e, "Could not try the question."));
    } finally {
      setBusy(false);
    }
  };

  const r = result?.r;
  const source = r?.item_id ? items.find((i) => i.item_id === r.item_id) : undefined;

  return (
    <Panel className="space-y-3 px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
          <FlaskConical className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Try it</h3>
          <p className="text-[12px] text-ink-muted">
            Type a question as a patient would ask it and see what the agent would do. Nothing is recorded on a patient.
            {live ? "" : " Sample data: a simple keyword match stands in for the real one."}
          </p>
        </div>
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); run(); }}
      >
        <label htmlFor="kb-try" className="sr-only">Patient question</label>
        <input
          id="kb-try"
          value={question}
          maxLength={MAX_QUESTION}
          placeholder="e.g. When can I take a shower?"
          onChange={(e) => setQuestion(e.target.value)}
          className="h-9 min-w-0 flex-1 rounded-xl border border-black/[0.08] bg-white px-3 text-[14px] text-ink outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
        />
        <button
          type="submit"
          disabled={!q || busy || (!live && !settings)}
          className="inline-flex items-center gap-1.5 rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" /> {busy ? "Asking…" : "Ask"}
        </button>
      </form>

      {r ? (
        <div
          aria-live="polite"
          className={cn("rounded-2xl p-4 ring-1 ring-inset",
            r.outcome === "answered" ? "bg-brand/[0.06] ring-brand/25" : "bg-brand-yellow/15 ring-brand-yellow/40")}
        >
          <p className="text-[12px] text-ink-muted">&ldquo;{result!.question}&rdquo;</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {r.outcome === "answered"
              ? <Pill tone="good" icon={BookOpenCheck}>Answered</Pill>
              : <Pill tone="routine">Deferred to a nurse</Pill>}
            <Pill tone="muted">{r.category_label || categoryLabelOf(r.category)}</Pill>
            {typeof r.confidence === "number" ? (
              <Pill tone="muted">Match {Math.round(r.confidence * 100)}%</Pill>
            ) : null}
          </div>
          {r.outcome === "answered" ? (
            <>
              <p className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">The patient would hear</p>
              <p className="mt-0.5 text-[14px] leading-relaxed text-ink">&ldquo;{r.answer ?? ""}&rdquo;</p>
              <p className="mt-2 text-[12px] text-ink-soft">
                Source: {source ? <>&ldquo;{source.question}&rdquo;</> : "Clinic Q&A"}
                {typeof r.item_version === "number" ? ` · version ${r.item_version}` : ""}
              </p>
            </>
          ) : (
            <p className="mt-3 text-[13px] text-ink">{r.reason}</p>
          )}
          {r.note ? (
            <p className="mt-2 rounded-xl bg-white/80 px-3 py-2 text-[12px] text-ink-soft ring-1 ring-inset ring-black/[0.05]">{r.note}</p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
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
      `Approve this answer?\n\nPatients will hear this answer word for word:\n\n"${item.answer}"`)) return;
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
          <h3 className="font-display text-[15px] font-semibold text-ink">Clinic Q&amp;A</h3>
          <p className="text-[12px] text-ink-muted">Only approved entries are used. Editing an approved entry sends it back to draft.</p>
        </div>
        <button
          type="button"
          disabled={!settings || items === null}
          onClick={() => setEditing({ mode: "new" })}
          className="inline-flex items-center gap-1.5 rounded-full bg-brand-deep px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> New entry
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
          <label htmlFor="kb-search" className="sr-only">Search the Q&amp;A</label>
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
        <p className="px-5 pb-5 text-[13px] text-ink-muted">{error ? "Could not load the Q&A." : "Loading…"}</p>
      ) : shown.length === 0 ? (
        <EmptyState
          title={items.length ? "No entries match" : "No Q&A yet"}
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
          <DialogTitle className="font-display">{item ? "Edit entry" : "New Q&A entry"}</DialogTitle>
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
            hint="One per line."
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
            hint="Patients hear this word for word. Write it as you would say it."
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

          <Field label="Category" htmlFor="kb-cat" hint="Medication and symptom questions are never answered, so they are not offered.">
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
