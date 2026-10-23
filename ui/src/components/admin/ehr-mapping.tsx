"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, FileText, KeyRound, ListChecks, Plus, PlugZap, ShieldAlert, Stethoscope, Trash2, UserRound, X } from "lucide-react";
import {
  AnswerType,
  CODE_SYSTEM_PRESETS,
  DEFAULT_PRIORITY,
  EhrPreview,
  EhrProblem,
  EhrProfile,
  EhrQuestion,
  EhrQuestionsResponse,
  Json,
  JsonObject,
  Path,
  TASK_PRIORITIES,
  TASK_STATUSES,
  TIERS,
  TIER_LABEL,
  addObservation,
  addUseCase,
  advancedFields,
  advancedItemKeys,
  bool,
  codeMapRows,
  excludedOf,
  getIn,
  itemOf,
  missingCodes,
  presetOf,
  problemsUnder,
  removeObservation,
  removeUseCase,
  sampleAnswers,
  setCodeAt,
  setDocumentEnabled,
  setIn,
  setItemType,
  setLinkId,
  setResourceEnabled,
  setValueType,
  str,
  toggleSend,
  typeOptions,
  ucLabel,
  unmapItem,
  useCases,
} from "@/lib/ehr-export";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, Check, Field, Note, ProblemLines, Select, TabBar, TextInput, Toggle } from "./ehr-ui";
import { FhirPane } from "./ehr-preview";

/**
 * The §4.2a mapper: one card per part of the profile, editing the profile
 * JSON in place (lib/ehr-export.ts reducers). Problems come from the live
 * validate call; the right column re-renders the preview as the admin types.
 */

type Edit = (fn: (p: EhrProfile) => EhrProfile) => void;

const ANSWER_LABEL: Record<AnswerType, string> = {
  boolean: "boolean",
  integer: "integer",
  decimal: "decimal",
  string: "string (text)",
  coding: "coding (code map)",
  date: "date",
  quantity: "quantity (with unit)",
};

function fmtAnswer(v: Json | undefined): string {
  if (v === undefined) return "";
  if (Array.isArray(v)) return v.map((x) => (x && typeof x === "object" && !Array.isArray(x) ? str((x as JsonObject).text) : String(x))).join(" · ");
  return typeof v === "string" ? `"${v}"` : JSON.stringify(v);
}

// --------------------------------------------------------------------------- code map

function SystemPicker({ value, onChange, label = "Code system", allowEmpty }: { value: string; onChange: (v: string) => void; label?: string; allowEmpty?: boolean }) {
  const preset = value ? presetOf(value) : allowEmpty ? "custom" : "custom";
  return (
    <div className="grid min-w-0 gap-2 sm:grid-cols-[150px_minmax(0,1fr)]">
      <Field label={label}>
        <Select
          value={preset}
          options={[...CODE_SYSTEM_PRESETS.map((c) => ({ value: c.key, label: c.label })), { value: "custom", label: "Custom / local" }]}
          onChange={(k) => {
            const p = CODE_SYSTEM_PRESETS.find((c) => c.key === k);
            onChange(p ? p.system : "");
          }}
        />
      </Field>
      <Field label="System URL">
        <TextInput value={value} onChange={onChange} placeholder="https://fhir.example.org/CodeSystem/…" spellCheck={false} />
      </Field>
    </div>
  );
}

function CodeMapGrid({ base, spec, q, edit }: { base: Path; spec: JsonObject; q: EhrQuestion | undefined; edit: Edit }) {
  const rows = codeMapRows(q, spec);
  const missing = missingCodes(q, spec);
  const [extra, setExtra] = useState("");
  const codes = (spec.codes && typeof spec.codes === "object" ? spec.codes : {}) as Record<string, JsonObject>;
  return (
    <div className="min-w-0 space-y-2 rounded-xl bg-mist/60 p-2.5">
      <SystemPicker value={str(spec.system)} onChange={(v) => edit((p) => setIn(p, [...base, "system"], v || undefined))} />
      <div className="min-w-0">
        <div className="hidden grid-cols-[110px_repeat(3,minmax(0,1fr))] gap-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-ink-muted sm:grid">
          <span>App answer</span>
          <span>Code</span>
          <span>Display</span>
          <span>System (if not the map&apos;s)</span>
        </div>
        <div className="space-y-1.5">
          {rows.map((ans) => {
            const c = codes[ans] ?? {};
            const isMissing = missing.includes(ans);
            return (
              <div key={ans} className="grid min-w-0 grid-cols-2 gap-1.5 rounded-lg sm:grid-cols-[110px_repeat(3,minmax(0,1fr))] sm:gap-2">
                <span className={cn("col-span-2 self-center font-mono text-[12.5px] font-semibold sm:col-span-1", isMissing ? "text-brand-coral-dark" : "text-ink")}>
                  {ans}
                  {q && !q.choices.includes(ans) ? <span className="ml-1 text-[10.5px] font-normal text-ink-muted">(not a choice)</span> : null}
                </span>
                <TextInput aria-label={`Code for ${ans}`} value={str(c.code)} invalid={isMissing} placeholder="code" onChange={(v) => edit((p) => setCodeAt(p, base, ans, "code", v))} spellCheck={false} />
                <TextInput aria-label={`Display for ${ans}`} value={str(c.display)} placeholder="display" onChange={(v) => edit((p) => setCodeAt(p, base, ans, "display", v))} />
                <TextInput aria-label={`System for ${ans}`} className="col-span-2 sm:col-span-1" value={str(c.system)} placeholder={str(spec.system) || "system"} onChange={(v) => edit((p) => setCodeAt(p, base, ans, "system", v))} spellCheck={false} />
              </div>
            );
          })}
        </div>
        {!q?.choices.length ? (
          <div className="mt-2 flex min-w-0 items-end gap-2">
            <Field label="Add a code for an app value" className="flex-1">
              <TextInput value={extra} onChange={setExtra} placeholder="e.g. high" />
            </Field>
            <Button
              size="sm"
              variant="secondary"
              disabled={!extra.trim()}
              onClick={() => {
                const k = extra.trim().toLowerCase();
                edit((p) => setCodeAt(p, base, k, "code", k));
                setExtra("");
              }}
            >
              Add
            </Button>
          </div>
        ) : null}
      </div>
      {missing.length ? (
        <p className="flex items-start gap-1.5 text-[12px] font-semibold text-brand-coral-dark">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> No code for {missing.map((m) => `"${m}"`).join(", ")}: such an answer is left out.
        </p>
      ) : null}
    </div>
  );
}

function UnitFields({ base, spec, edit }: { base: Path; spec: JsonObject; edit: Edit }) {
  return (
    <div className="grid min-w-0 gap-2 rounded-xl bg-mist/60 p-2.5 sm:grid-cols-3">
      <Field label="Unit (shown)">
        <TextInput value={str(spec.unit)} onChange={(v) => edit((p) => setIn(p, [...base, "unit"], v || undefined))} placeholder="e.g. mg/dL" invalid={!str(spec.unit)} />
      </Field>
      <Field label="Unit code" hint="Defaults to the unit">
        <TextInput value={str(spec.unit_code)} onChange={(v) => edit((p) => setIn(p, [...base, "unit_code"], v || undefined))} placeholder={str(spec.unit) || "UCUM code"} />
      </Field>
      <Field label="Unit system" hint="UCUM by default">
        <TextInput value={str(spec.unit_system)} onChange={(v) => edit((p) => setIn(p, [...base, "unit_system"], v || undefined))} placeholder="http://unitsofmeasure.org" spellCheck={false} />
      </Field>
    </div>
  );
}

// --------------------------------------------------------------------------- questions table

function QuestionRow({ uc, qid, q, profile, problems, sample, edit, order }: { uc: number; qid: string; q: EhrQuestion | undefined; profile: EhrProfile; problems: EhrProblem[]; sample: Json | undefined; edit: Edit; order: string[] }) {
  const item = itemOf(profile, uc, qid);
  const excluded = excludedOf(profile, uc).includes(qid);
  const base: Path = ["use_cases", uc, "questionnaire_response", "items", qid];
  const rowProblems = problemsUnder(problems, `/use_cases/${uc}/questionnaire_response/items/${qid}`);
  const advanced = advancedItemKeys(item);
  const type = str(item?.type) as AnswerType | "";
  const hasError = rowProblems.some((p) => p.level === "error");
  return (
    <li className={cn("min-w-0 rounded-2xl bg-white/80 p-3 ring-1", hasError ? "ring-brand-red/40" : "ring-black/[0.05]")} data-question={qid}>
      <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1.5">
        <Toggle checked={!excluded} onChange={(v) => edit((p) => toggleSend(p, uc, qid, v))} label={<span className="sr-only">Send {qid}</span>} title={excluded ? "Never sent (excluded)" : "Sent when answered"} />
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="font-mono text-[13px] font-semibold text-ink">{qid}</span>
            <span className="rounded-full bg-mist px-1.5 py-0.5 text-[10.5px] font-semibold text-ink-soft">{q ? q.type.replace(/_/g, " ") : "not in the spec"}</span>
            {excluded ? <span className="rounded-full bg-brand-orange/20 px-1.5 py-0.5 text-[10.5px] font-semibold text-ink">never sent</span> : null}
            {!item && !excluded ? <span className="rounded-full bg-brand-yellow/30 px-1.5 py-0.5 text-[10.5px] font-semibold text-ink">not mapped</span> : null}
          </p>
          {q ? <p className="mt-0.5 text-[12.5px] text-ink-soft">{q.ask}</p> : null}
          <p className="mt-0.5 flex min-w-0 flex-wrap gap-x-3 text-[11.5px] text-ink-muted">
            {q?.choices.length ? <span>choices: {q.choices.join(" · ")}</span> : null}
            {sample !== undefined ? <span className="min-w-0 break-words">sample answer: <span className="font-mono text-ink-soft">{fmtAnswer(sample)}</span></span> : null}
          </p>
        </div>
      </div>
      <div className={cn("mt-2.5 grid min-w-0 gap-2 sm:grid-cols-[minmax(0,140px)_minmax(0,190px)_minmax(0,1fr)_auto]", excluded && "opacity-60")}>
        <Field label="EHR linkId">
          <TextInput
            value={str(item?.link_id)}
            onChange={(v) => edit((p) => setLinkId(p, uc, qid, v, order))}
            placeholder={item ? "" : "Not mapped"}
            invalid={!!item && !str(item.link_id)}
            spellCheck={false}
            aria-label={`EHR linkId for ${qid}`}
          />
        </Field>
        <Field label="Answer type">
          <Select
            aria-label={`Answer type for ${qid}`}
            disabled={!item}
            value={type}
            options={typeOptions(q, item).map((t) => ({ value: t, label: ANSWER_LABEL[t] + (q && !q.allowed_types.includes(t) && !(item?.value_map) ? " (does not fit)" : "") }))}
            onChange={(t) => edit((p) => setItemType(p, uc, qid, t))}
          />
        </Field>
        <Field label="EHR item text (optional)">
          <TextInput disabled={!item} value={str(item?.text)} onChange={(v) => edit((p) => setIn(p, [...base, "text"], v || undefined))} placeholder="as the EHR's Questionnaire words it" />
        </Field>
        <div className="flex items-end">
          {item ? (
            <Button variant="ghost" size="sm" onClick={() => edit((p) => unmapItem(p, uc, qid))} title="Stop mapping this question" aria-label={`Unmap ${qid}`}>
              <X className="mr-1 h-3.5 w-3.5" /> Unmap
            </Button>
          ) : null}
        </div>
      </div>
      {item && type === "coding" ? <div className="mt-2"><CodeMapGrid base={base} spec={item} q={q} edit={edit} /></div> : null}
      {item && type === "quantity" ? <div className="mt-2"><UnitFields base={base} spec={item} edit={edit} /></div> : null}
      {advanced.length ? (
        <p className="mt-2 text-[11.5px] text-ink-muted">
          Advanced: <span className="font-mono">{advanced.join(", ")}</span>. Kept as is; edit in YAML.
        </p>
      ) : null}
      {rowProblems.length ? <div className="mt-2"><ProblemLines problems={rowProblems} compact /></div> : null}
    </li>
  );
}

function QuestionsCard({ uc, profile, questions, questionsError, problems, sample, edit }: { uc: number; profile: EhrProfile; questions: EhrQuestionsResponse | null; questionsError: string | null; problems: EhrProblem[]; sample: Record<string, Json>; edit: Edit }) {
  const qs = questions?.questions ?? [];
  const items = (getIn(profile, ["use_cases", uc, "questionnaire_response", "items"]) ?? {}) as JsonObject;
  const unknown = Object.keys(items).filter((k) => !qs.some((q) => q.id === k));
  const order = qs.map((q) => q.id);
  const qr = getIn(profile, ["use_cases", uc, "questionnaire_response"]) as JsonObject | undefined;
  const mapped = qs.filter((q) => q.id in items).length;
  return (
    <Card
      title={`Questions${questions ? ` (spec version ${questions.spec_version})` : ""}`}
      icon={<ListChecks className="h-4 w-4 text-brand-deep" />}
      actions={<span className="text-[12px] text-ink-muted">{mapped} of {qs.length} mapped</span>}
    >
      {questionsError ? <Note tone="error">{questionsError}</Note> : null}
      {!questions && !questionsError ? <p className="py-4 text-[13px] text-ink-muted">Loading the use case&apos;s questions…</p> : null}
      <div className="mb-3 grid min-w-0 gap-2 sm:grid-cols-[200px_minmax(0,1fr)]">
        <Field label="Unmapped answers" hint="Text only if the EHR's Questionnaire has those items: a strict server rejects unknown linkIds.">
          <Select
            value={str(qr?.unmapped, "skip") as "skip" | "text"}
            options={[
              { value: "skip", label: "Skip (listed in the preview)" },
              { value: "text", label: "Send as text" },
            ]}
            onChange={(v) => edit((p) => setIn(p, ["use_cases", uc, "questionnaire_response", "unmapped"], v))}
          />
        </Field>
        {str(qr?.unmapped) === "text" ? (
          <Field label="linkId prefix for unmapped answers">
            <TextInput value={str(qr?.unmapped_prefix)} onChange={(v) => edit((p) => setIn(p, ["use_cases", uc, "questionnaire_response", "unmapped_prefix"], v || undefined))} placeholder="e.g. app." />
          </Field>
        ) : null}
      </div>
      <ul className="space-y-2">
        {qs.map((q) => (
          <QuestionRow key={q.id} uc={uc} qid={q.id} q={q} profile={profile} problems={problems} sample={sample[q.id]} edit={edit} order={order} />
        ))}
        {unknown.map((qid) => (
          <QuestionRow key={qid} uc={uc} qid={qid} q={undefined} profile={profile} problems={problems} sample={sample[qid]} edit={edit} order={order} />
        ))}
      </ul>
    </Card>
  );
}

// --------------------------------------------------------------------------- resource cards

function QuestionnaireCard({ uc, profile, problems, edit }: { uc: number; profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  const qr = getIn(profile, ["use_cases", uc, "questionnaire_response"]) as JsonObject | null | undefined;
  const on = !!qr && qr.enabled !== false;
  const base: Path = ["use_cases", uc, "questionnaire_response"];
  return (
    <Card
      title="QuestionnaireResponse"
      icon={<FileText className="h-4 w-4 text-brand-deep" />}
      actions={<Toggle checked={on} onChange={(v) => edit((p) => setResourceEnabled(p, uc, "questionnaire_response", v))} label={on ? "On" : "Off"} />}
    >
      <div className={cn("grid min-w-0 gap-2 sm:grid-cols-2", !on && "opacity-60")}>
        <Field label="Questionnaire canonical URL" hint="The EHR's Questionnaire, optionally |version." className="sm:col-span-2">
          <TextInput disabled={!qr} value={str(qr?.questionnaire)} onChange={(v) => edit((p) => setIn(p, [...base, "questionnaire"], v))} placeholder="https://fhir.example.org/Questionnaire/…|1" spellCheck={false} invalid={on && !str(qr?.questionnaire)} />
        </Field>
        <Field label="Author display">
          <TextInput disabled={!qr} value={str(qr?.author_display, "Automated outreach (patient-reported)")} onChange={(v) => edit((p) => setIn(p, [...base, "author_display"], v))} />
        </Field>
      </div>
      <p className="mt-2 text-[11.5px] text-ink-muted">Fixed: identifier = the call id, status completed, subject = the patient, authored = the call&apos;s end.</p>
      <div className="mt-2"><ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/questionnaire_response`).filter((p) => !p.path.includes(".items.") && !p.path.includes("/items/"))} compact /></div>
    </Card>
  );
}

function TaskCard({ uc, profile, problems, edit }: { uc: number; profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  const t = getIn(profile, ["use_cases", uc, "task"]) as JsonObject | null | undefined;
  const on = !!t && t.enabled !== false;
  const base: Path = ["use_cases", uc, "task"];
  const status = { resolved: "completed", open: "requested", ...((t?.status as JsonObject) ?? {}) } as Record<string, string>;
  const priority = { ...DEFAULT_PRIORITY, ...((t?.priority as JsonObject) ?? {}) } as Record<string, string>;
  const owner = (t?.owner as JsonObject) ?? {};
  const code = (t?.code as JsonObject | null | undefined) ?? null;
  const set = (path: Path, v: unknown) => edit((p) => setIn(p, [...base, ...path], v));
  return (
    <Card title="Task (nurse follow-up)" icon={<Stethoscope className="h-4 w-4 text-brand-deep" />} actions={<Toggle checked={on} onChange={(v) => edit((p) => setResourceEnabled(p, uc, "task", v))} label={on ? "On" : "Off"} />}>
      <div className={cn("min-w-0 space-y-3", !on && "pointer-events-none opacity-60")}>
        <div className="grid min-w-0 gap-2 sm:grid-cols-3">
          <Field label="Emit when">
            <Select
              value={str(t?.emit_when, "resolved") as "resolved"}
              options={[
                { value: "resolved", label: "The sign-off resolved a follow-up" },
                { value: "needs_follow_up", label: "The tier needed a callback" },
                { value: "always", label: "Every sign-off" },
              ]}
              onChange={(v) => set(["emit_when"], v)}
            />
          </Field>
          <Field label="Status when resolved">
            <Select value={status.resolved as "completed"} options={TASK_STATUSES.map((s) => ({ value: s, label: s }))} onChange={(v) => set(["status"], { ...status, resolved: v })} />
          </Field>
          <Field label="Status when open">
            <Select value={status.open as "requested"} options={TASK_STATUSES.map((s) => ({ value: s, label: s }))} onChange={(v) => set(["status"], { ...status, open: v })} />
          </Field>
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-[12px] font-semibold text-ink-soft">Tier → priority</p>
          <div className="grid min-w-0 gap-1.5 sm:grid-cols-2">
            {TIERS.map((tier) => (
              <div key={tier} className="flex min-w-0 items-center gap-2 rounded-lg bg-mist/60 px-2 py-1">
                <span className="min-w-0 flex-1 text-[12.5px] text-ink">{TIER_LABEL[tier]}</span>
                <Select aria-label={`Priority for ${tier}`} className="w-28" value={priority[tier] as "routine"} options={TASK_PRIORITIES.map((s) => ({ value: s, label: s }))} onChange={(v) => set(["priority"], { ...priority, [tier]: v })} />
              </div>
            ))}
          </div>
        </div>
        <div className="grid min-w-0 gap-2 sm:grid-cols-3">
          <Field label="Owner">
            <Select
              value={str(owner.mode, "display") as "display"}
              options={[
                { value: "display", label: "The signer, as a name" },
                { value: "identifier", label: "The signer, as an identifier" },
                { value: "none", label: "No owner" },
              ]}
              onChange={(v) => set(["owner", "mode"], v)}
            />
          </Field>
          {str(owner.mode) === "identifier" ? (
            <Field label="Owner identifier system" className="sm:col-span-2">
              <TextInput value={str(owner.identifier_system)} onChange={(v) => set(["owner", "identifier_system"], v)} invalid={!str(owner.identifier_system)} placeholder="https://fhir.example.org/identifiers/staff-email" spellCheck={false} />
            </Field>
          ) : null}
          <Field label="Intent">
            <Select value={str(t?.intent, "order") as "order"} options={["order", "plan", "proposal"].map((v) => ({ value: v as "order", label: v }))} onChange={(v) => set(["intent"], v)} />
          </Field>
          <Field label="Description" className="sm:col-span-2">
            <TextInput value={str(t?.description, "RN follow-up after automated outreach")} onChange={(v) => set(["description"], v)} />
          </Field>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <Check checked={bool(t?.include_nurse_note, true)} onChange={(v) => set(["include_nurse_note"], v)} label="Include the nurse's note" />
          <Check checked={bool(t?.include_reason, true)} onChange={(v) => set(["include_reason"], v)} label="Include the triage reason" />
        </div>
        <div className="grid min-w-0 gap-2 sm:grid-cols-3">
          <Field label="Task code (optional)">
            <TextInput value={str(code?.code)} onChange={(v) => set(["code"], v || str(code?.system) || str(code?.display) ? { ...(code ?? {}), code: v } : undefined)} placeholder="e.g. rn-callback" />
          </Field>
          <Field label="Code display">
            <TextInput disabled={!code} value={str(code?.display)} onChange={(v) => set(["code", "display"], v || undefined)} />
          </Field>
          <Field label="Code system">
            <TextInput disabled={!code} value={str(code?.system)} onChange={(v) => set(["code", "system"], v || undefined)} invalid={!!code && !str(code.system) && !str(t?.code_system)} spellCheck={false} />
          </Field>
        </div>
      </div>
      <div className="mt-2"><ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/task`)} compact /></div>
    </Card>
  );
}

function ObservationsCard({ uc, profile, questions, problems, edit }: { uc: number; profile: EhrProfile; questions: EhrQuestion[]; problems: EhrProblem[]; edit: Edit }) {
  const obs = (getIn(profile, ["use_cases", uc, "observations"]) as JsonObject[] | undefined) ?? [];
  const [pick, setPick] = useState("");
  return (
    <Card title="Observations" icon={<ListChecks className="h-4 w-4 text-brand-deep" />} actions={<span className="text-[12px] text-ink-muted">{obs.length} row{obs.length === 1 ? "" : "s"}</span>}>
      <p className="mb-2 text-[12px] text-ink-muted">Selected answers as Observations (e.g. a pain score with a LOINC code). Patient-reported: status final, derivedFrom the QuestionnaireResponse. No answer, no Observation.</p>
      <ul className="space-y-2">
        {obs.map((ob, j) => {
          const base: Path = ["use_cases", uc, "observations", j];
          const q = questions.find((x) => x.id === str(ob.source));
          const code = (ob.code as JsonObject) ?? {};
          const type = str(ob.type) as AnswerType;
          return (
            <li key={j} className="min-w-0 rounded-2xl bg-white/80 p-3 ring-1 ring-black/[0.05]">
              <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                <Field label="Source question">
                  <Select aria-label="Source question" value={str(ob.source)} options={[...questions.map((x) => ({ value: x.id, label: x.id })), ...(q ? [] : [{ value: str(ob.source), label: `${str(ob.source)} (not in the spec)` }])]} onChange={(v) => edit((p) => setIn(p, [...base, "source"], v))} />
                </Field>
                <Field label="Value type">
                  <Select aria-label="Value type" value={type} options={typeOptions(q, ob).map((t) => ({ value: t, label: ANSWER_LABEL[t] }))} onChange={(t) => edit((p) => setValueType(p, base, t))} />
                </Field>
                <div className="flex items-end">
                  <Button variant="ghost" size="sm" onClick={() => edit((p) => removeObservation(p, uc, j))} aria-label={`Remove observation ${str(ob.source)}`}>
                    <Trash2 className="mr-1 h-3.5 w-3.5" /> Remove
                  </Button>
                </div>
              </div>
              <div className="mt-2 min-w-0">
                <SystemPicker label="Observation code system" value={str(code.system)} onChange={(v) => edit((p) => setIn(p, [...base, "code", "system"], v))} />
              </div>
              <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-3">
                <Field label="Code">
                  <TextInput value={str(code.code)} onChange={(v) => edit((p) => setIn(p, [...base, "code", "code"], v))} invalid={!str(code.code)} spellCheck={false} />
                </Field>
                <Field label="Display" className="sm:col-span-1">
                  <TextInput value={str(code.display)} onChange={(v) => edit((p) => setIn(p, [...base, "code", "display"], v || undefined))} />
                </Field>
                <Field label="Category">
                  <Select value={str(ob.category, "survey") as "survey"} options={["survey", "vital-signs", "exam", "social-history"].map((v) => ({ value: v as "survey", label: v }))} onChange={(v) => edit((p) => setIn(p, [...base, "category"], v))} />
                </Field>
              </div>
              {type === "coding" ? <div className="mt-2"><CodeMapGrid base={base} spec={ob} q={q} edit={edit} /></div> : null}
              {type === "quantity" ? <div className="mt-2"><UnitFields base={base} spec={ob} edit={edit} /></div> : null}
              <div className="mt-2"><ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/observations/${j}`)} compact /></div>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex min-w-0 flex-wrap items-end gap-2">
        <Field label="Add an observation from" className="min-w-[180px] flex-1 sm:flex-none">
          <Select value={pick} options={questions.map((x) => ({ value: x.id, label: x.id }))} onChange={setPick} />
        </Field>
        <Button
          size="sm"
          variant="secondary"
          disabled={!pick}
          onClick={() => {
            edit((p) => addObservation(p, uc, pick));
            setPick("");
          }}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> Add observation
        </Button>
      </div>
      <div className="mt-2"><ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/observations`).filter((p) => /observations$/.test(p.path))} compact /></div>
    </Card>
  );
}

function DocumentBlock({ uc, which, profile, problems, edit }: { uc: number; which: "summary" | "transcript"; profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  const doc = (getIn(profile, ["use_cases", uc, which]) as JsonObject | undefined) ?? {};
  const on = doc.enabled === true;
  const ack = doc.phi_acknowledged === true;
  const base: Path = ["use_cases", uc, which];
  return (
    <div className={cn("min-w-0 rounded-2xl p-3 ring-1", on ? "bg-brand-orange/10 ring-brand-orange/40" : "bg-white/80 ring-black/[0.05]")}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-[13.5px] font-semibold text-ink">{which === "summary" ? "Call summary" : "Call transcript"}</p>
        <Toggle
          checked={on}
          disabled={!ack && !on}
          title={!ack ? "Tick the privacy acknowledgement first" : undefined}
          onChange={(v) => edit((p) => setDocumentEnabled(p, uc, which, v, ack))}
          label={on ? "On" : "Off"}
        />
      </div>
      <p className="mt-1 text-[12px] text-ink-soft">
        {which === "summary" ? "Built by code from the disposition: channel, date, tier, reason, signer and the nurse's note." : "Read from the call's transcript only when this is on: the patient's own words."}
      </p>
      <div className="mt-2">
        <Check
          checked={ack}
          onChange={(v) => edit((p) => (v ? setIn(p, [...base, "phi_acknowledged"], true) : setIn(setIn(p, [...base, "phi_acknowledged"], false), [...base, "enabled"], false)))}
          label="Our privacy office approved sending this to the EHR"
        />
      </div>
      {on ? (
        <div className="mt-2 space-y-2">
          <Note tone="warn">This sends free-text PHI to the EHR. It is off by default; keep it on only with your privacy office&apos;s approval.</Note>
          <div className="grid min-w-0 gap-2 sm:grid-cols-2">
            <Field label="As">
              <Select value={str(doc.resource, "DocumentReference") as "DocumentReference"} options={["DocumentReference", "Communication"].map((v) => ({ value: v as "DocumentReference", label: v }))} onChange={(v) => edit((p) => setIn(p, [...base, "resource"], v))} />
            </Field>
            {which === "transcript" ? (
              <Field label="Language">
                <Select
                  value={str(doc.language, "original") as "original"}
                  options={[
                    { value: "original", label: "As spoken (original)" },
                    { value: "english" as "original", label: "English translation, when there is one" },
                  ]}
                  onChange={(v) => edit((p) => setIn(p, [...base, "language"], v))}
                />
              </Field>
            ) : null}
          </div>
        </div>
      ) : null}
      <div className="mt-2"><ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/${which}`)} compact /></div>
    </div>
  );
}

function DocumentsCard(props: { uc: number; profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  return (
    <Card title="Summary and transcript" icon={<ShieldAlert className="h-4 w-4 text-brand-coral-dark" />}>
      <div className="grid min-w-0 gap-2 lg:grid-cols-2">
        <DocumentBlock which="summary" {...props} />
        <DocumentBlock which="transcript" {...props} />
      </div>
      <p className="mt-2 text-[11.5px] text-ink-muted">Recordings are never exported.</p>
    </Card>
  );
}

function PatientCard({ profile, problems, edit }: { profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  const pt = (profile.patient as JsonObject) ?? {};
  const set = (path: Path, v: unknown) => edit((p) => setIn(p, path, v));
  return (
    <Card title="Patient and identifiers" icon={<UserRound className="h-4 w-4 text-brand-deep" />}>
      <div className="grid min-w-0 gap-2 sm:grid-cols-2">
        <Field label="MRN identifier system" hint="Patient?identifier=<system>|<mrn>. The exporter never creates or guesses a patient." className="sm:col-span-2">
          <TextInput value={str(pt.identifier_system)} onChange={(v) => set(["patient", "identifier_system"], v)} invalid={!str(pt.identifier_system)} spellCheck={false} />
        </Field>
        <Field label="When the patient is not found">
          <Select
            value={str(pt.not_found, "retry") as "retry"}
            options={[
              { value: "retry", label: "Retry (stays pending), then fail" },
              { value: "fail" as "retry", label: "Fail at once" },
              { value: "skip" as "retry", label: "Skip" },
            ]}
            onChange={(v) => set(["patient", "not_found"], v)}
          />
        </Field>
        <Field label="Call identifier system" hint="Every resource carries <system>|<call id>: the idempotency key.">
          <TextInput value={str(getIn(profile, ["identifiers", "system"]))} onChange={(v) => set(["identifiers", "system"], v)} invalid={!str(getIn(profile, ["identifiers", "system"]))} spellCheck={false} />
        </Field>
      </div>
      <div className="mt-3 space-y-2">
        <Toggle checked={bool(pt.search_with_birthdate, false)} onChange={(v) => set(["patient", "search_with_birthdate"], v)} label="Also match on date of birth" />
        {bool(pt.search_with_birthdate, false) ? <Note tone="warn">The exporter then reads the patient&apos;s date of birth and sends it in the search (never in a resource). It is PHI the default does not send.</Note> : null}
        <Toggle checked={bool(pt.include_display_name, false)} onChange={(v) => set(["patient", "include_display_name"], v)} label="Put the patient's name in references" />
        <Toggle checked={bool(profile.validate_output, true)} onChange={(v) => set(["validate_output"], v)} label="Check every bundle's FHIR R4 structure before sending" />
      </div>
      <div className="mt-2"><ProblemLines problems={[...problemsUnder(problems, "/patient"), ...problemsUnder(problems, "/identifiers")]} compact /></div>
    </Card>
  );
}

function ConnectionCard({ profile, problems, edit }: { profile: EhrProfile; problems: EhrProblem[]; edit: Edit }) {
  const target = (profile.target as JsonObject) ?? {};
  const auth = (target.auth as JsonObject) ?? {};
  const type = str(auth.type, "none");
  const secretName = str(auth.secret).replace(/^secret:\/\//, "");
  const set = (path: Path, v: unknown) => edit((p) => setIn(p, ["target", ...path], v));
  return (
    <Card
      title="Connection"
      icon={<PlugZap className="h-4 w-4 text-brand-deep" />}
      actions={
        <span title="Available when the export service is deployed" className="inline-flex">
          <Button size="sm" variant="secondary" disabled aria-label="Test connection (available when the export service is deployed)">
            Test connection
          </Button>
        </span>
      }
    >
      <div className="grid min-w-0 gap-2 sm:grid-cols-2">
        <Field label="FHIR base URL" hint="https only (http only for localhost). Not a secret." className="sm:col-span-2">
          <TextInput value={str(target.base_url)} onChange={(v) => set(["base_url"], v)} placeholder="https://fhir.example.org/R4" spellCheck={false} />
        </Field>
        <Field label="FHIR version">
          <TextInput value={str(target.fhir_version, "R4")} onChange={() => {}} disabled />
        </Field>
        <Field label="Auth type">
          <Select
            value={type as "none"}
            options={[
              { value: "none", label: "None (test servers)" },
              { value: "bearer" as "none", label: "Bearer token" },
              { value: "client_credentials" as "none", label: "OAuth2 client credentials (not built yet)" },
              { value: "smart_backend" as "none", label: "SMART Backend Services (not built yet)" },
            ]}
            onChange={(v) => set(["auth", "type"], v)}
          />
        </Field>
        {type !== "none" ? (
          <Field label="Secret name (Secret Manager)" hint="The name only: the value never comes here. Optionally name#version." className="sm:col-span-2">
            <div className="flex min-w-0 items-center gap-1">
              <span className="shrink-0 font-mono text-[12.5px] text-ink-muted">secret://</span>
              <TextInput
                value={secretName}
                onChange={(v) => set(["auth", "secret"], v.trim() ? `secret://${v.trim().replace(/^secret:\/\//, "")}` : "")}
                placeholder="ehr-fhir-token"
                autoComplete="off"
                spellCheck={false}
                aria-label="Secret name"
                invalid={!secretName}
              />
            </div>
          </Field>
        ) : null}
        {type === "client_credentials" || type === "smart_backend" ? (
          <>
            <Field label="Client id">
              <TextInput value={str(auth.client_id)} onChange={(v) => set(["auth", "client_id"], v)} spellCheck={false} />
            </Field>
            <Field label="Token URL">
              <TextInput value={str(auth.token_url)} onChange={(v) => set(["auth", "token_url"], v)} spellCheck={false} />
            </Field>
            <Field label="Scope" className="sm:col-span-2">
              <TextInput value={str(auth.scope)} onChange={(v) => set(["auth", "scope"], v || undefined)} spellCheck={false} />
            </Field>
          </>
        ) : null}
        <Field label="Submit">
          <Select
            value={str(target.submit, "transaction") as "transaction"}
            options={[
              { value: "transaction", label: "One transaction Bundle per call" },
              { value: "individual" as "transaction", label: "One request per resource" },
            ]}
            onChange={(v) => set(["submit"], v)}
          />
        </Field>
        <Field label="Write mode">
          <Select
            value={str(target.write_mode, "conditional_update") as "conditional_update"}
            options={[
              { value: "conditional_update", label: "Conditional update (PUT ?identifier=)" },
              { value: "conditional_create" as "conditional_update", label: "Conditional create (If-None-Exist)" },
            ]}
            onChange={(v) => set(["write_mode"], v)}
          />
        </Field>
        <Field label="Timeout (seconds)">
          <TextInput type="number" min={1} max={120} value={String(target.timeout_s ?? 30)} onChange={(v) => set(["timeout_s"], v === "" ? undefined : Number(v))} />
        </Field>
      </div>
      <div className="mt-2"><ProblemLines problems={problemsUnder(problems, "/target")} compact /></div>
    </Card>
  );
}

function ProfileCard({ profile, edit }: { profile: EhrProfile; edit: Edit }) {
  return (
    <Card title="Profile" icon={<KeyRound className="h-4 w-4 text-brand-deep" />}>
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_90px]">
        <Field label="Id">
          <TextInput value={str(profile.id)} onChange={(v) => edit((p) => setIn(p, ["id"], v))} spellCheck={false} />
        </Field>
        <Field label="Name">
          <TextInput value={str(profile.name)} onChange={(v) => edit((p) => setIn(p, ["name"], v))} />
        </Field>
        <Field label="Revision">
          <TextInput type="number" min={1} value={String(profile.version ?? 1)} onChange={(v) => edit((p) => setIn(p, ["version"], Math.max(1, Number(v) || 1)))} />
        </Field>
        <Field label="Description" className="sm:col-span-3">
          <TextInput value={str(profile.description)} onChange={(v) => edit((p) => setIn(p, ["description"], v || undefined))} />
        </Field>
      </div>
    </Card>
  );
}

export function ProblemsPanel({ problems, busy, error }: { problems: EhrProblem[]; busy: boolean; error: string | null }) {
  const errors = problems.filter((p) => p.level === "error");
  const warnings = problems.filter((p) => p.level === "warning");
  return (
    <Card title={`Problems: ${errors.length} error${errors.length === 1 ? "" : "s"}, ${warnings.length} warning${warnings.length === 1 ? "" : "s"}`} icon={<AlertTriangle className={cn("h-4 w-4", errors.length ? "text-brand-red" : "text-brand-orange")} />} actions={busy ? <span className="text-[11.5px] text-ink-muted">checking…</span> : null}>
      {error ? <Note tone="error">{error}</Note> : null}
      {!problems.length && !error ? <p className="text-[12.5px] text-brand-dark">No problems: the profile checks clean against the use-case specs.</p> : null}
      {problems.length ? (
        <ul className="max-h-72 space-y-1.5 overflow-auto">
          {[...errors, ...warnings].map((p, i) => (
            <li key={i} className="min-w-0">
              <ProblemLines problems={[p]} compact />
              <p className="ml-5 break-all font-mono text-[10.5px] text-ink-muted">{p.path}</p>
            </li>
          ))}
        </ul>
      ) : null}
      {errors.length ? <p className="mt-2 text-[11.5px] font-semibold text-brand-red-dark">Errors block Activate. Warnings don&apos;t.</p> : null}
    </Card>
  );
}

// --------------------------------------------------------------------------- the tab

export function MappingTab({
  profile,
  edit,
  ucIndex,
  onUc,
  questions,
  questionsError,
  questionsFor,
  problems,
  validating,
  validateError,
  campaigns,
  preview,
  previewBusy,
  previewError,
}: {
  profile: EhrProfile;
  edit: Edit;
  ucIndex: number;
  onUc: (i: number) => void;
  questions: EhrQuestionsResponse | null;
  questionsError: string | null;
  questionsFor: (campaignId: string) => EhrQuestion[];
  problems: EhrProblem[];
  validating: boolean;
  validateError: string | null;
  campaigns: { id: string; name: string }[] | undefined;
  preview: EhrPreview | null;
  previewBusy: boolean;
  previewError: string | null;
}) {
  const ucs = useCases(profile);
  const [addPick, setAddPick] = useState("");
  const uc = Math.min(ucIndex, Math.max(0, ucs.length - 1));
  const current = ucs[uc];
  const sample = useMemo(() => (preview ? sampleAnswers(preview.source_record) : {}), [preview]);
  const advanced = useMemo(() => advancedFields(profile), [profile]);
  const versions = Array.isArray(current?.spec_versions) ? (current.spec_versions as Json[]).join(", ") : "";
  return (
    <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,440px)] 2xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-4">
        <ProfileCard profile={profile} edit={edit} />
        <ConnectionCard profile={profile} problems={problems} edit={edit} />
        <PatientCard profile={profile} problems={problems} edit={edit} />

        <div className="min-w-0 space-y-3 rounded-[28px] bg-brand-deep/[0.04] p-3 ring-1 ring-brand-deep/10 sm:p-4">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-ink-muted">Use cases</p>
            <div className="min-w-0 flex-1">
              <TabBar
                label="Use cases"
                value={String(uc)}
                onChange={(v) => onUc(Number(v))}
                tabs={ucs.map((u, i) => ({ value: String(i), label: `${ucLabel(str(u.campaign_id), campaigns)}${Array.isArray(u.spec_versions) && u.spec_versions.length ? ` v${u.spec_versions.join(",")}` : ""}` }))}
              />
            </div>
          </div>
          <div className="flex min-w-0 flex-wrap items-end gap-2">
            <Field label="Add use case" className="min-w-[180px] flex-1 sm:flex-none">
              <Select value={addPick} options={(campaigns ?? []).map((c) => ({ value: c.id, label: `${c.name}${ucs.some((u) => u.campaign_id === c.id) ? " (mapped)" : ""}` }))} onChange={setAddPick} />
            </Field>
            <Button
              size="sm"
              variant="secondary"
              disabled={!addPick}
              onClick={() => {
                edit((p) => addUseCase(p, addPick));
                onUc(ucs.length);
                setAddPick("");
              }}
            >
              <Plus className="mr-1 h-3.5 w-3.5" /> Add use case
            </Button>
            {current ? (
              <>
                <Field label="Spec versions (blank = every version)" className="min-w-[160px] flex-1 sm:flex-none">
                  <TextInput
                    value={versions}
                    onChange={(v) => edit((p) => setIn(p, ["use_cases", uc, "spec_versions"], v.split(",").map((x) => Number(x.trim())).filter((n) => Number.isInteger(n) && n > 0)))}
                    placeholder="e.g. 1, 2"
                  />
                </Field>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    if (window.confirm(`Remove the ${str(current.campaign_id)} mapping from this draft?`)) {
                      edit((p) => removeUseCase(p, uc));
                      onUc(Math.max(0, uc - 1));
                    }
                  }}
                >
                  <Trash2 className="mr-1 h-3.5 w-3.5" /> Remove use case
                </Button>
              </>
            ) : null}
          </div>
          {current ? (
            <>
              <ProblemLines problems={problemsUnder(problems, `/use_cases/${uc}/campaign_id`).concat(problemsUnder(problems, `/use_cases/${uc}/spec_versions`))} compact />
              <QuestionsCard uc={uc} profile={profile} questions={questions} questionsError={questionsError} problems={problems} sample={str(current.campaign_id) === str(getIn(preview?.source_record, ["campaign_id"]), "post_op") ? sample : {}} edit={edit} />
              <QuestionnaireCard uc={uc} profile={profile} problems={problems} edit={edit} />
              <TaskCard uc={uc} profile={profile} problems={problems} edit={edit} />
              <ObservationsCard uc={uc} profile={profile} questions={questionsFor(str(current.campaign_id))} problems={problems} edit={edit} />
              <DocumentsCard uc={uc} profile={profile} problems={problems} edit={edit} />
            </>
          ) : (
            <Note>No use case is mapped yet. Add one above.</Note>
          )}
        </div>

        {advanced.length ? (
          <Note>
            Advanced fields this editor does not show are kept exactly as they are (advanced: edit in YAML): <span className="break-all font-mono">{advanced.join(", ")}</span>
          </Note>
        ) : null}
      </div>
      <aside className="min-w-0 space-y-4 xl:sticky xl:top-4 xl:self-start">
        <ProblemsPanel problems={problems} busy={validating} error={validateError} />
        <FhirPane preview={preview} busy={previewBusy} error={previewError} />
      </aside>
    </div>
  );
}
