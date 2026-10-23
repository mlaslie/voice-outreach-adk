/**
 * Where Admin → EHR export reads and writes: the API (§4.2b, through the
 * BFF), or, in sample mode, this tab's memory with the client-side stub
 * (./ehr-export-demo.ts). The panel only sees this interface.
 */

import { api } from "./api";
import {
  EhrExportState,
  EhrPreview,
  EhrPreviewSources,
  EhrProblem,
  EhrProfile,
  EhrQuestionsResponse,
  EhrSimulation,
  EhrVersion,
  OutboxRecord,
  PreviewSource,
  normalizePreview,
} from "./ehr-export";
import {
  DEMO_SOURCES,
  EXAMPLE_PROFILE,
  demoOutbox,
  demoPreview,
  demoQuestions,
  demoSimulate,
  demoValidate,
  demoVersions,
} from "./ehr-export-demo";

export type YamlVersion = "draft" | "active" | number;

export interface EhrBackend {
  live: boolean;
  state(): Promise<EhrExportState>;
  questions(campaignId: string): Promise<EhrQuestionsResponse>;
  validate(profile: EhrProfile): Promise<EhrProblem[]>;
  saveDraft(body: { profile: EhrProfile; note?: string } | { yaml: string; note?: string }): Promise<EhrVersion>;
  resetDraft(): Promise<EhrVersion | null>;
  activate(note?: string): Promise<EhrVersion>;
  versions(): Promise<EhrVersion[]>;
  restore(n: number): Promise<EhrVersion>;
  /** Download the YAML (live: a link to the API; sample: a JSON file, which is YAML too). */
  download(version: YamlVersion, profileFor: (v: YamlVersion) => EhrProfile | null): void;
  example(): Promise<EhrProfile>;
  sources(): Promise<EhrPreviewSources>;
  preview(profile: EhrProfile | undefined, source: PreviewSource): Promise<EhrPreview>;
  simulate(profile: EhrProfile | undefined, source: PreviewSource): Promise<EhrSimulation>;
  outbox(status?: string): Promise<OutboxRecord[]>;
  requeue(callId: string): Promise<void>;
}

function clickDownload(href: string, filename?: string) {
  const a = document.createElement("a");
  a.href = href;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function saveJsonFile(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json" }));
  clickDownload(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const liveEhrBackend: EhrBackend = {
  live: true,
  state: () => api.getEhrExport(),
  questions: (cid) => api.getEhrQuestions(cid),
  validate: async (profile) => (await api.validateEhrProfile(profile)).problems ?? [],
  saveDraft: (body) => api.saveEhrDraft(body),
  resetDraft: () => api.resetEhrDraft(),
  activate: (note) => api.activateEhrDraft(note),
  versions: async () => (await api.getEhrVersions()).versions ?? [],
  restore: (n) => api.restoreEhrVersion(n),
  download: (version) => clickDownload(api.ehrYamlUrl(version)),
  example: async () => (await api.getEhrExample()).profile,
  sources: () => api.getEhrPreviewSources(),
  preview: async (profile, source) => normalizePreview(await api.previewEhr(profile ? { profile, source } : { source })),
  simulate: (profile, source) => api.simulateEhr(profile ? { profile, source } : { source }),
  outbox: async (status) => (await api.getEhrOutbox(status)).records ?? [],
  requeue: async (callId) => {
    await api.requeueEhrOutbox(callId);
  },
};

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const NOW = () => new Date().toISOString();
const DEMO_USER = "you (sample)";

/** Sample mode: versions and the outbox live in this object (one per panel); nothing leaves the tab. */
export function demoEhrBackend(): EhrBackend {
  let versions = demoVersions();
  let outbox = demoOutbox();
  const find = (s: string) => versions.find((v) => v.status === s) ?? null;
  const state = (): EhrExportState => ({
    active_version: find("active")?.version ?? null,
    draft_version: find("draft")?.version ?? null,
    active: clone(find("active")),
    draft: clone(find("draft")),
  });
  const nextN = () => Math.max(0, ...versions.map((v) => v.version)) + 1;
  const putDraft = (profile: EhrProfile, note?: string): EhrVersion => {
    const d = find("draft");
    const v: EhrVersion = { version: d?.version ?? nextN(), status: "draft", profile: clone(profile), problems: demoValidate(profile), saved_by: DEMO_USER, saved_at: NOW(), note: note ?? d?.note ?? null };
    versions = [v, ...versions.filter((x) => x.status !== "draft")];
    return clone(v);
  };
  const delay = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 120));
  return {
    live: false,
    state: () => delay(state()),
    questions: (cid) => delay(demoQuestions(cid)),
    validate: (profile) => delay(demoValidate(profile)),
    saveDraft: async (body) => {
      if ("yaml" in body) throw new Error("Sample data: YAML files need the API. Upload the profile as JSON (JSON is YAML) to try it here.");
      return delay(putDraft(body.profile, body.note));
    },
    resetDraft: async () => {
      const a = find("active");
      return delay(a?.profile ? putDraft(a.profile, `Reset to version ${a.version}`) : null);
    },
    activate: async (note) => {
      const d = find("draft");
      if (!d?.profile) throw new Error("There is no draft to activate.");
      const problems = demoValidate(d.profile);
      if (problems.some((p) => p.level === "error")) throw new Error("The draft has errors: fix them first.");
      versions = versions.map((v) => (v.status === "active" ? { ...v, status: "retired" as const } : v));
      const act: EhrVersion = { ...d, status: "active", problems, activated_by: DEMO_USER, activated_at: NOW(), note: note ?? d.note ?? null };
      versions = [act, ...versions.filter((v) => v !== d)];
      return delay(clone(act));
    },
    versions: () => delay(clone(versions).map(({ profile: _p, ...rest }) => rest as EhrVersion)),
    restore: async (n) => {
      const src = versions.find((v) => v.version === n);
      if (!src?.profile) throw new Error(`No version ${n}.`);
      const fresh: EhrVersion = { ...clone(src), version: nextN(), status: "draft", saved_by: DEMO_USER, saved_at: NOW(), activated_by: null, activated_at: null, note: `Restored from version ${n}` };
      versions = [fresh, ...versions.filter((v) => v.status !== "draft")];
      return delay(clone(fresh));
    },
    download: (version, profileFor) => {
      const v = typeof version === "number" ? (versions.find((x) => x.version === version)?.profile ?? null) : profileFor(version);
      if (v) saveJsonFile(`ehr-export-${version}.json`, v);
    },
    example: () => delay(clone(EXAMPLE_PROFILE)),
    sources: () => delay(DEMO_SOURCES),
    preview: (profile) => delay(demoPreview(profile ?? find("draft")?.profile ?? EXAMPLE_PROFILE)),
    simulate: (profile) => {
      const p = profile ?? find("draft")?.profile ?? EXAMPLE_PROFILE;
      return delay(demoSimulate(p, demoPreview(p)));
    },
    outbox: (status) => delay(clone(status ? outbox.filter((r) => r.status === status) : outbox)),
    requeue: async (callId) => {
      outbox = outbox.map((r) => (r.call_id === callId ? { ...r, status: "pending_export", last_error: null } : r));
      await delay(null);
    },
  };
}
