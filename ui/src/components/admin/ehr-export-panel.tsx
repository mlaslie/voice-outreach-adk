"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Download, FileUp, FlaskConical, History, Inbox, Loader2, RefreshCw, RotateCcw, Rocket, Save, Sparkles } from "lucide-react";
import { ApiError } from "@/lib/api";
import {
  EhrExportState,
  EhrPreview,
  EhrPreviewSources,
  EhrProblem,
  EhrProfile,
  EhrQuestion,
  EhrQuestionsResponse,
  EhrVersion,
  OUTBOX_STATUSES,
  OutboxRecord,
  PreviewSource,
  canRequeue,
  hasErrors,
  jsonDiff,
  readablePath,
  shortJson,
  str,
  uploadBody,
  useCases,
} from "@/lib/ehr-export";
import { EhrBackend, YamlVersion, demoEhrBackend, liveEhrBackend } from "@/lib/ehr-export-client";
import { DEMO_NOTE } from "@/lib/ehr-export-demo";
import { fmtDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import { useCampaigns } from "@/components/campaigns-provider";
import { EmptyState, Pill, Tone } from "@/components/nurse/parts";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Card, Note, TabBar, TextInput, useDebounced } from "./ehr-ui";
import { MappingTab } from "./ehr-mapping";
import { PreviewTab } from "./ehr-preview";

/**
 * Admin → Integrations → EHR export (docs/EHR-EXPORT-PLAN.md §4): the mapper,
 * the side-by-side preview with simulate, versions and the outbox. Live: the
 * §4.2b API (admin build only; writes audited). Sample data (?data=demo): the
 * example profile and fictional sample, with a client-side stub; nothing is saved.
 */

type Tab = "mapping" | "preview" | "versions" | "outbox";

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError || e instanceof Error ? e.message || fallback : fallback;
}

/** The problems a 409 (activate) or 422 (an unparseable profile) carries: {detail: {message, problems}}. */
function errProblems(e: unknown): EhrProblem[] | null {
  if (!(e instanceof ApiError)) return null;
  const d = (e.body as { detail?: { problems?: unknown } } | undefined)?.detail;
  return d && Array.isArray(d.problems) ? (d.problems as EhrProblem[]) : null;
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const when = (iso?: string | null) => (iso ? fmtDateTime(iso, { month: "short", day: "numeric" }) : "—");

const STATUS_TONE: Record<string, Tone> = { draft: "routine", active: "good", retired: "muted" };
const OUTBOX_TONE: Record<string, Tone> = { pending_export: "info", exported: "good", failed: "emergency", skipped: "warn", withdrawn: "muted", withdrawn_done: "muted" };

export function EhrExportPanel({ live }: { live: boolean }) {
  const backend = useMemo<EhrBackend>(() => (live ? liveEhrBackend : demoEhrBackend()), [live]);
  const campaignsResp = useCampaigns();
  const [tab, setTab] = useState<Tab>("mapping");
  const [state, setState] = useState<EhrExportState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profile, setProfile] = useState<EhrProfile | null>(null);
  /** The saved draft (else the active profile), as JSON: the editor is dirty when it differs. */
  const [baseline, setBaseline] = useState<string | null>(null);
  const [uc, setUc] = useState(0);
  const [questions, setQuestions] = useState<Record<string, EhrQuestionsResponse>>({});
  const [qErrors, setQErrors] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<EhrProblem[]>([]);
  const [validating, setValidating] = useState(false);
  const [validateError, setValidateError] = useState<string | null>(null);
  const [sources, setSources] = useState<EhrPreviewSources | null>(null);
  const [source, setSource] = useState<PreviewSource | null>(null);
  const [preview, setPreview] = useState<EhrPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  const [versionsKey, setVersionsKey] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const s = await backend.state();
      setState(s);
      const p = s.draft?.profile ?? s.active?.profile ?? null;
      setProfile(p ? clone(p) : null);
      // Dirty means "differs from what is saved": the draft, else the active profile.
      setBaseline(p ? JSON.stringify(p) : null);
      if (s.draft?.problems) setProblems(s.draft.problems);
    } catch (e) {
      setLoadError(errText(e, "Could not load the EHR export profile."));
    }
  }, [backend]);

  useEffect(() => {
    load();
    backend.sources().then(setSources, () => setSources({ samples: [], calls: [] }));
  }, [backend, load]);

  const edit = useCallback((fn: (p: EhrProfile) => EhrProfile) => setProfile((p) => (p ? fn(p) : p)), []);
  const dirty = !!profile && JSON.stringify(profile) !== baseline;

  // The questions of every mapped use case (the table, and observation sources).
  const ucIds = useMemo(() => [...new Set(useCases(profile).map((u) => str(u.campaign_id)))], [profile]);
  useEffect(() => {
    for (const cid of ucIds) {
      if (!cid || questions[cid] || qErrors[cid]) continue;
      backend.questions(cid).then(
        (r) => setQuestions((q) => ({ ...q, [cid]: r })),
        (e) => setQErrors((q) => ({ ...q, [cid]: errText(e, `Could not load the questions of ${cid}.`) })),
      );
    }
  }, [ucIds, questions, qErrors, backend]);

  // Live validation (debounced): profile_problems on the unsaved profile.
  const settled = useDebounced(profile, 400);
  const vSeq = useRef(0);
  useEffect(() => {
    if (!settled) return;
    const n = ++vSeq.current;
    setValidating(true);
    backend.validate(settled).then(
      (ps) => {
        if (n !== vSeq.current) return;
        setProblems(ps);
        setValidateError(null);
        setValidating(false);
      },
      (e) => {
        if (n !== vSeq.current) return;
        setProblems(errProblems(e) ?? []);
        setValidateError(errProblems(e) ? null : errText(e, "Could not check the profile."));
        setValidating(false);
      },
    );
  }, [settled, backend]);

  // The preview source: the current use case's sample, else the first one offered.
  const ucCampaign = str(useCases(profile)[uc]?.campaign_id);
  useEffect(() => {
    if (!sources || source) return;
    const s = sources.samples.find((x) => x.campaign_id === ucCampaign) ?? sources.samples[0];
    if (s) setSource({ sample: s.campaign_id });
    else if (sources.calls[0]) setSource({ call_id: sources.calls[0].call_id });
  }, [sources, source, ucCampaign]);

  // Live preview (debounced) from the unsaved profile.
  const pSeq = useRef(0);
  useEffect(() => {
    if (!settled || !source) return;
    const n = ++pSeq.current;
    setPreviewBusy(true);
    backend.preview(settled, source).then(
      (r) => {
        if (n !== pSeq.current) return;
        setPreview(r);
        setPreviewError(null);
        setPreviewBusy(false);
      },
      (e) => {
        if (n !== pSeq.current) return;
        setPreviewError(errText(e, "Could not build the preview."));
        const ps = errProblems(e);
        if (ps) setProblems(ps);
        setPreviewBusy(false);
      },
    );
  }, [settled, source, backend]);

  const campaigns = useMemo(() => {
    const list = (campaignsResp?.campaigns ?? []).map((c) => ({ id: c.id, name: c.name }));
    for (const id of state?.use_cases ?? []) if (!list.some((c) => c.id === id)) list.push({ id, name: id });
    return list;
  }, [campaignsResp, state]);

  const questionsFor = useCallback((cid: string): EhrQuestion[] => questions[cid]?.questions ?? [], [questions]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      const ps = errProblems(e);
      if (ps) setProblems(ps);
      toast.error(errText(e, `${label} failed.`));
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = () =>
    run("Save draft", async () => {
      if (!profile) return;
      const v = await backend.saveDraft({ profile });
      const saved = v.profile ?? profile;
      setProfile(clone(saved));
      setBaseline(JSON.stringify(saved));
      if (v.problems) setProblems(v.problems);
      setState(await backend.state());
      setVersionsKey((k) => k + 1);
      toast.success(live ? `Draft saved as version ${v.version}.` : `${DEMO_NOTE} (kept in this tab only).`);
    });

  const activate = (note: string) =>
    run("Activate", async () => {
      if (!profile) return;
      if (dirty) await backend.saveDraft({ profile });
      const v = await backend.activate(note || undefined);
      setActivating(false);
      await load();
      setVersionsKey((k) => k + 1);
      toast.success(live ? `Version ${v.version} is active: the exporter uses it from its next run.` : `Version ${v.version} is active. ${DEMO_NOTE}.`);
    });

  const resetDraft = () =>
    run("Reset draft", async () => {
      if (!window.confirm("Replace the draft with a copy of the active profile? Unsaved changes are lost.")) return;
      await backend.resetDraft();
      await load();
      setVersionsKey((k) => k + 1);
      toast.success("The draft is a copy of the active profile again.");
    });

  const upload = (file: File) =>
    run("Upload", async () => {
      if (file.size > 200_000) throw new Error("The file is too large (at most 200 KB).");
      const text = await file.text();
      const v = await backend.saveDraft(uploadBody(text));
      await load();
      setVersionsKey((k) => k + 1);
      const errs = (v.problems ?? []).filter((p) => p.level === "error").length;
      toast.success(`${file.name} saved as the draft${errs ? `, with ${errs} error${errs === 1 ? "" : "s"} to fix` : ""}.`);
    });

  const download = (v: YamlVersion) =>
    backend.download(v, (which) => (which === "draft" ? profile : (state?.active?.profile ?? null)));

  const startFromExample = () =>
    run("Load the example", async () => {
      const p = await backend.example();
      setProfile(clone(p));
      setBaseline(null);
      setUc(0);
      toast.success("The example is in the editor. Save it as a draft to keep it.");
    });

  const status = state?.draft
    ? `Draft v${state.draft.version}, saved by ${state.draft.saved_by} ${when(state.draft.saved_at)}`
    : profile
      ? "No draft saved yet"
      : "";
  const active = state?.active ? `Active: v${state.active.version}${state.active.activated_at ? `, ${when(state.active.activated_at)}` : ""}` : "Nothing active: the exporter sends nothing";
  const blocked = hasErrors(problems);

  return (
    <div className="min-w-0 space-y-4">
      {!live ? (
        <div className="flex items-center gap-2 rounded-2xl bg-brand-yellow/25 px-4 py-2 text-[13px] font-semibold text-ink ring-1 ring-brand-yellow/50">
          <FlaskConical className="h-4 w-4 shrink-0" /> {DEMO_NOTE}. The example profile and a fictional call; the preview is computed in this browser.
        </div>
      ) : null}
      <Note tone="info">
        <strong className="font-semibold text-ink">Mapping only: nothing is sent to an EHR yet.</strong> The mapping, preview and
        simulate work on real call results, but the service that sends them to your EHR is not built, so Test connection is off.
      </Note>
      {loadError ? (
        <Note tone="error">
          {loadError}{" "}
          <button className="font-semibold underline" onClick={load}>
            Try again
          </button>
        </Note>
      ) : null}

      <TabBar<Tab>
        label="EHR export"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "mapping", label: "Mapping" },
          { value: "preview", label: "Preview" },
          { value: "versions", label: <><History className="h-3.5 w-3.5" /> Versions</> },
          { value: "outbox", label: <><Inbox className="h-3.5 w-3.5" /> Outbox</> },
        ]}
      />

      {tab === "mapping" || tab === "preview" ? (
        <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-2xl bg-white/70 px-3 py-2 ring-1 ring-black/[0.05]">
          <div className="min-w-0 flex-1 text-[12px] text-ink-soft">
            <p className="truncate">
              {status}
              {dirty ? <span className="ml-2 rounded-full bg-brand-orange/25 px-2 py-0.5 font-semibold text-ink">unsaved changes</span> : null}
            </p>
            <p className="truncate text-ink-muted">{active}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" onClick={saveDraft} disabled={!profile || !dirty || !!busy}>
              {busy === "Save draft" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />} Save draft
            </Button>
            <span title={blocked ? "Fix the errors first: errors block Activate" : undefined} className="inline-flex">
              <Button size="sm" variant="outline" onClick={() => setActivating(true)} disabled={!profile || blocked || !!busy}>
                <Rocket className="mr-1.5 h-3.5 w-3.5" /> Activate
              </Button>
            </span>
            <Button size="sm" variant="secondary" onClick={() => download("draft")} disabled={!state?.draft && !(profile && !live)} title="Download the saved draft">
              <Download className="mr-1.5 h-3.5 w-3.5" /> Draft YAML
            </Button>
            <Button size="sm" variant="secondary" onClick={() => download("active")} disabled={!state?.active} title="Download the active profile">
              <Download className="mr-1.5 h-3.5 w-3.5" /> Active YAML
            </Button>
            <Button size="sm" variant="secondary" onClick={() => fileRef.current?.click()} disabled={!!busy}>
              <FileUp className="mr-1.5 h-3.5 w-3.5" /> Upload YAML/JSON
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml,.json,application/json,text/yaml"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) upload(f);
              }}
            />
            <Button size="sm" variant="ghost" onClick={resetDraft} disabled={!state?.active || !!busy}>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Reset draft to active
            </Button>
          </div>
        </div>
      ) : null}

      {(tab === "mapping" || tab === "preview") && state && !profile ? (
        <Card title="No export profile yet" icon={<Sparkles className="h-4 w-4 text-brand-deep" />}>
          <p className="mb-3 text-[13px] text-ink-soft">Nothing is exported until a profile is active. Start from the worked example (a fictional EHR and local codes), then adapt it to your EHR.</p>
          <Button onClick={startFromExample} disabled={!!busy}>
            <Sparkles className="mr-1.5 h-4 w-4" /> Start from the example
          </Button>
        </Card>
      ) : null}
      {!state && !loadError ? <p className="py-10 text-center text-[13px] text-ink-muted">Loading…</p> : null}

      {tab === "mapping" && profile ? (
        <MappingTab
          profile={profile}
          edit={edit}
          ucIndex={uc}
          onUc={setUc}
          questions={questions[ucCampaign] ?? null}
          questionsError={qErrors[ucCampaign] ?? null}
          questionsFor={questionsFor}
          problems={problems}
          validating={validating}
          validateError={validateError}
          campaigns={campaigns}
          preview={preview}
          previewBusy={previewBusy}
          previewError={previewError}
        />
      ) : null}
      {tab === "preview" && profile ? (
        <PreviewTab
          preview={preview}
          busy={previewBusy}
          error={previewError}
          sources={sources}
          source={source}
          onSource={setSource}
          simulate={() => (source ? backend.simulate(profile, source) : Promise.reject(new Error("Choose a source first.")))}
          live={live}
        />
      ) : null}
      {tab === "versions" ? <VersionsTab backend={backend} reloadKey={versionsKey} onRestored={() => { load(); setTab("mapping"); }} profileFor={(w) => (w === "draft" ? profile : (state?.active?.profile ?? null))} /> : null}
      {tab === "outbox" ? <OutboxTab backend={backend} /> : null}

      <ActivateDialog open={activating} onOpenChange={setActivating} active={state?.active ?? null} draft={profile} busy={busy === "Activate"} problems={problems} onConfirm={activate} />
    </div>
  );
}

function ActivateDialog({ open, onOpenChange, active, draft, busy, problems, onConfirm }: { open: boolean; onOpenChange: (v: boolean) => void; active: EhrVersion | null; draft: EhrProfile | null; busy: boolean; problems: EhrProblem[]; onConfirm: (note: string) => void }) {
  const [note, setNote] = useState("");
  // The profile's own revision is set by the API on save; leave it out of the diff.
  const diff = useMemo(() => (open && draft ? jsonDiff(active?.profile ? { ...active.profile, version: null } : undefined, { ...draft, version: null }) : []), [open, draft, active]);
  const warnings = problems.filter((p) => p.level === "warning").length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-[min(720px,calc(100vw-24px))] overflow-y-auto rounded-3xl bg-white">
        <DialogHeader>
          <DialogTitle>Activate this draft?</DialogTitle>
          <DialogDescription>
            The exporter uses the active profile from its next run{active ? `; version ${active.version} is retired and can be restored later` : ""}. Unsaved changes are saved first.
            {warnings ? ` ${warnings} warning${warnings === 1 ? "" : "s"} remain (they do not block).` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0">
          <p className="mb-1 text-[12.5px] font-semibold text-ink">{active ? `Changes from active version ${active.version}` : "Nothing is active yet: the whole profile becomes active"} ({diff.length})</p>
          {diff.length ? (
            <ul className="max-h-[45vh] space-y-1 overflow-auto rounded-xl bg-mist/60 p-2 font-mono text-[11.5px]">
              {diff.map((d, i) => (
                <li key={i} className="min-w-0 break-all">
                  <span className={cn("mr-1 font-semibold", d.kind === "added" ? "text-brand-dark" : d.kind === "removed" ? "text-brand-red-dark" : "text-brand-blue-dark")}>{d.kind === "added" ? "+" : d.kind === "removed" ? "−" : "~"}</span>
                  <span className="text-ink">{readablePath(d.path)}</span>
                  {d.kind === "changed" ? (
                    <span className="text-ink-soft">: {shortJson(d.before)} → {shortJson(d.after)}</span>
                  ) : d.path === "" ? null : (
                    <span className="text-ink-soft">: {shortJson(d.kind === "added" ? d.after : d.before)}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12.5px] text-ink-muted">The draft is the same as the active profile.</p>
          )}
        </div>
        <label className="block text-[12px] font-semibold text-ink-soft">
          Note (optional, kept with the version)
          <TextInput value={note} onChange={setNote} placeholder="e.g. Codes confirmed with the EHR team" className="mt-1" />
        </label>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => onConfirm(note)} disabled={busy}>
            {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-1.5 h-4 w-4" />} Activate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VersionsTab({ backend, reloadKey, onRestored, profileFor }: { backend: EhrBackend; reloadKey: number; onRestored: () => void; profileFor: (v: YamlVersion) => EhrProfile | null }) {
  const [rows, setRows] = useState<EhrVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    backend.versions().then(setRows, (e) => setError(errText(e, "Could not load the versions.")));
  }, [backend]);
  useEffect(() => load(), [load, reloadKey]);
  const restore = async (n: number) => {
    if (!window.confirm(`Copy version ${n} into a new draft? The current draft is replaced.`)) return;
    try {
      const v = await backend.restore(n);
      toast.success(`Version ${n} restored as draft version ${v.version}.`);
      load();
      onRestored();
    } catch (e) {
      toast.error(errText(e, "Could not restore that version."));
    }
  };
  return (
    <Card title="Versions" icon={<History className="h-4 w-4 text-brand-deep" />} actions={<Button size="sm" variant="ghost" onClick={load}><RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Refresh</Button>}>
      {error ? <Note tone="error">{error}</Note> : null}
      {rows && !rows.length ? <EmptyState title="No versions yet" hint="Saving a draft creates the first one." /> : null}
      {!rows && !error ? <p className="py-6 text-center text-[13px] text-ink-muted">Loading…</p> : null}
      <ul className="space-y-2">
        {(rows ?? []).map((v) => (
          <li key={v.version} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl bg-white/80 p-3 ring-1 ring-black/[0.05]">
            <div className="flex items-center gap-2">
              <span className="font-display text-[15px] font-semibold text-ink">v{v.version}</span>
              <Pill tone={STATUS_TONE[v.status] ?? "muted"}>{v.status}</Pill>
            </div>
            <div className="min-w-0 flex-1 text-[12px] text-ink-soft">
              <p className="break-words">Saved by {v.saved_by} · {when(v.saved_at)}</p>
              {v.activated_at ? <p className="break-words">Activated by {v.activated_by ?? "—"} · {when(v.activated_at)}</p> : null}
              {v.note ? <p className="break-words text-ink-muted">{v.note}</p> : null}
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" variant="secondary" onClick={() => backend.download(v.version, profileFor)}>
                <Download className="mr-1.5 h-3.5 w-3.5" /> YAML
              </Button>
              {v.status !== "draft" ? (
                <Button size="sm" variant="outline" onClick={() => restore(v.version)}>
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Restore as draft
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function OutboxTab({ backend }: { backend: EhrBackend }) {
  const [status, setStatus] = useState<string>("");
  const [rows, setRows] = useState<OutboxRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    backend.outbox(status || undefined).then(setRows, (e) => setError(errText(e, "Could not load the outbox.")));
  }, [backend, status]);
  useEffect(() => load(), [load]);
  const requeue = async (id: string) => {
    try {
      await backend.requeue(id);
      toast.success(`${id} is back in the queue (pending export).`);
      load();
    } catch (e) {
      toast.error(errText(e, "Could not requeue that record."));
    }
  };
  return (
    <Card title="Outbox" icon={<Inbox className="h-4 w-4 text-brand-deep" />} actions={<Button size="sm" variant="ghost" onClick={load}><RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Refresh</Button>}>
      <p className="mb-2 text-[12px] text-ink-muted">One record per signed-off call. Failed and skipped records can be requeued once the cause is fixed (for example, the profile or the EHR&apos;s patient record).</p>
      <TabBar
        label="Outbox status"
        value={status}
        onChange={setStatus}
        tabs={[{ value: "", label: "All" }, ...OUTBOX_STATUSES.map((s) => ({ value: s as string, label: s.replace(/_/g, " ") }))]}
      />
      {error ? <div className="mt-2"><Note tone="error">{error}</Note></div> : null}
      {rows && !rows.length ? <EmptyState title="Nothing here" hint={status ? `No ${status.replace(/_/g, " ")} records.` : "No signed-off calls yet."} /> : null}
      <ul className="mt-2 space-y-2">
        {(rows ?? []).map((r) => (
          <li key={r.call_id} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5 rounded-2xl bg-white/80 p-3 ring-1 ring-black/[0.05]">
            <div className="min-w-0 flex-1">
              <p className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="break-all font-mono text-[12.5px] font-semibold text-ink">{r.call_id}</span>
                {r.label ? <span className="text-[12.5px] text-ink-soft">{r.label}</span> : null}
                <Pill tone={OUTBOX_TONE[r.status] ?? "muted"}>{r.status.replace(/_/g, " ")}</Pill>
              </p>
              <p className="mt-0.5 text-[12px] text-ink-muted">
                Signed off {when(r.created_at)}
                {r.exported_at ? ` · exported ${when(r.exported_at)}` : ""}
                {r.attempts != null ? ` · ${r.attempts} attempt${r.attempts === 1 ? "" : "s"}` : ""}
              </p>
              {r.last_error ? <p className="mt-0.5 break-words font-mono text-[11.5px] text-brand-red-dark">{r.last_error}</p> : null}
            </div>
            {canRequeue(r.status) ? (
              <Button size="sm" variant="outline" onClick={() => requeue(r.call_id)}>
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Requeue
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
