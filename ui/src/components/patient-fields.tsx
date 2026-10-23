"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { ClinicDateTimeInput, INPUT_LOOK } from "@/components/nurse/clinic-time-input";
import { CallListEntryInput, CareSetting, PlaceCallRequest } from "@/lib/types";
import { useLanguages } from "@/components/languages-provider";
import { useCampaigns } from "@/components/campaigns-provider";
import { campaignLabel, campaignName } from "@/lib/campaigns";
import { DEFAULT_LANGUAGE, callInEnglishHint, isAvailable, languageOptionSuffix, optionLabel } from "@/lib/languages";

const DOB_RE = /^\d{4}-\d{2}-\d{2}$/;
const PHONE_RE = /^\+[1-9]\d{6,14}$/;
const PATIENT_ID_RE = /^[A-Za-z0-9_.-]{1,64}$/;

export interface PatientFormState {
  phone: string;
  patient_name: string;
  patient_id: string;
  patient_dob: string;
  operation_name: string;
  operation_date: string;
  is_diabetic: boolean;
  /** Inpatient or outpatient care, or "" when not given. */
  care_setting: "" | CareSetting;
  /** Preferred language code (GET /api/languages); default en-US. */
  language: string;
  /** Call in English while the language is not available (held otherwise). */
  call_in_english: boolean;
  /** The entry's use case (GET /api/campaigns); "" = the default (post-op). */
  campaign_id?: string;
  /** Planned call time as the browser's datetime-local string, or "". */
  scheduled_for: string;
}

export const INITIAL_PATIENT_FORM: PatientFormState = {
  phone: "",
  patient_name: "",
  patient_id: "",
  patient_dob: "",
  operation_name: "",
  operation_date: "",
  is_diabetic: false,
  care_setting: "",
  language: DEFAULT_LANGUAGE,
  call_in_english: false,
  campaign_id: "",
  scheduled_for: "",
};

/** ISO (UTC) -> the value a datetime-local input wants, in local time. */
export function isoToLocalInput(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local string -> ISO with offset, or null when blank/invalid. */
export function localInputToIso(local: string): string | null {
  if (!local.trim()) return null;
  const d = new Date(local);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

function validateDob(v: string): string | null {
  if (!DOB_RE.test(v)) return "Use YYYY-MM-DD, e.g. 1950-03-15";
  const d = new Date(v + "T00:00:00Z");
  if (isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== v)
    return "Not a real calendar date";
  if (d > new Date()) return "Date of birth is in the future";
  return null;
}

function validateOpDate(v: string): string | null {
  if (!v) return null;
  if (!DOB_RE.test(v)) return "Use YYYY-MM-DD";
  const d = new Date(v + "T00:00:00Z");
  if (isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== v)
    return "Not a real calendar date";
  return null;
}

export function validatePatientForm(form: PatientFormState): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!PHONE_RE.test(form.phone.trim()))
    errors.phone = "E.164 format required, e.g. +12025550123";
  if (!form.patient_name.trim()) errors.patient_name = "Required";
  if (!PATIENT_ID_RE.test(form.patient_id.trim()))
    errors.patient_id = "Letters, numbers, _, . and - only, 1-64 chars";
  const dobErr = validateDob(form.patient_dob.trim());
  if (dobErr) errors.patient_dob = dobErr;
  const opDateErr = validateOpDate(form.operation_date.trim());
  if (opDateErr) errors.operation_date = opDateErr;
  if (form.scheduled_for.trim() && localInputToIso(form.scheduled_for) === null)
    errors.scheduled_for = "Not a valid date and time";
  return errors;
}

/** Trim and drop empty optional fields for the wire format. */
export function patientFormToRequest(form: PatientFormState): PlaceCallRequest {
  const body: PlaceCallRequest = {
    phone: form.phone.trim(),
    patient_name: form.patient_name.trim(),
    patient_id: form.patient_id.trim(),
    patient_dob: form.patient_dob.trim(),
    is_diabetic: form.is_diabetic,
  };
  if (form.operation_name.trim()) body.operation_name = form.operation_name.trim();
  if (form.operation_date.trim()) body.operation_date = form.operation_date.trim();
  if (form.care_setting) body.care_setting = form.care_setting;
  // Always sent, so an edit can change it (the API keeps them only when absent).
  body.language = form.language || DEFAULT_LANGUAGE;
  body.call_in_english = form.call_in_english;
  if (form.campaign_id?.trim()) body.campaign_id = form.campaign_id.trim();
  return body;
}

/**
 * The call-list shape: a patient plus an optional planned call time. The
 * care setting is always sent (null when blank) so an edit can clear it.
 */
export function entryFormToRequest(form: PatientFormState): CallListEntryInput {
  return {
    ...patientFormToRequest(form),
    care_setting: form.care_setting || null,
    // Always sent, so an edit can set it back to the default (null).
    campaign_id: form.campaign_id?.trim() || null,
    scheduled_for: localInputToIso(form.scheduled_for),
  };
}

interface PatientFieldsProps {
  form: PatientFormState;
  errors: Record<string, string>;
  /** Prefix so two forms on one page don't collide on element ids. */
  idPrefix?: string;
  /** Show the planned-call-time field (call-list entries only). */
  showSchedule?: boolean;
  /** Show the use-case select (GET /api/campaigns; the admin console's patients). */
  showCampaign?: boolean;
  onChange: (key: keyof PatientFormState, value: string | boolean) => void;
  onBlur: (key: keyof PatientFormState) => void;
}

export function PatientFields({
  form,
  errors,
  idPrefix = "",
  showSchedule = false,
  showCampaign = false,
  onChange,
  onBlur,
}: PatientFieldsProps) {
  const id = (k: string) => `${idPrefix}${k}`;
  const langs = useLanguages();
  const list = langs?.languages ?? [];
  const available = isAvailable(form.language, form.campaign_id, langs);
  const campaigns = useCampaigns();
  const defaultCampaign = campaigns?.default ?? "post_op";
  // The default is the blank option, whether stored as null or by its id.
  const campaignValue = form.campaign_id && form.campaign_id !== defaultCampaign ? form.campaign_id : "";

  return (
    <>
      {showSchedule && (
        <div className="space-y-1.5">
          <Label htmlFor={id("scheduled_for")}>Planned call time</Label>
          <ClinicDateTimeInput
            id={id("scheduled_for")}
            label="Planned call"
            value={form.scheduled_for}
            onChange={(v) => onChange("scheduled_for", v)}
            onBlur={() => onBlur("scheduled_for")}
            inputClassName={INPUT_LOOK}
          />
          {errors.scheduled_for && <p className="text-xs text-brand-red-dark">{errors.scheduled_for}</p>}
          <p className="text-xs text-ink-soft">
            The scheduler dials once, at this time. Leave blank to keep the patient off the automatic schedule and call by hand.
          </p>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor={id("phone")}>Phone number *</Label>
        <Input
          id={id("phone")}
          inputMode="tel"
          placeholder="+12025550123"
          value={form.phone}
          onChange={(e) => onChange("phone", e.target.value)}
          onBlur={() => onBlur("phone")}
        />
        {errors.phone && <p className="text-xs text-brand-red-dark">{errors.phone}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("patient_name")}>Patient name *</Label>
        <Input
          id={id("patient_name")}
          placeholder="Jane Doe"
          value={form.patient_name}
          onChange={(e) => onChange("patient_name", e.target.value)}
          onBlur={() => onBlur("patient_name")}
        />
        {errors.patient_name && <p className="text-xs text-brand-red-dark">{errors.patient_name}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("patient_id")}>Patient ID *</Label>
        <Input
          id={id("patient_id")}
          placeholder="PT-001"
          value={form.patient_id}
          onChange={(e) => onChange("patient_id", e.target.value)}
          onBlur={() => onBlur("patient_id")}
        />
        {errors.patient_id && <p className="text-xs text-brand-red-dark">{errors.patient_id}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("patient_dob")}>
          Date of birth * <span className="text-xs text-ink-soft font-normal">YYYY-MM-DD</span>
        </Label>
        <Input
          id={id("patient_dob")}
          inputMode="numeric"
          placeholder="1950-03-15"
          value={form.patient_dob}
          onChange={(e) => onChange("patient_dob", e.target.value)}
          onBlur={() => onBlur("patient_dob")}
        />
        {errors.patient_dob && <p className="text-xs text-brand-red-dark">{errors.patient_dob}</p>}
        <p className="text-xs text-ink-soft">Agent verifies this on call</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("operation_name")}>Operation name</Label>
        <Input
          id={id("operation_name")}
          placeholder="Knee replacement"
          value={form.operation_name}
          onChange={(e) => onChange("operation_name", e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("operation_date")}>Operation date</Label>
        <Input
          id={id("operation_date")}
          inputMode="numeric"
          placeholder="2026-07-01"
          value={form.operation_date}
          onChange={(e) => onChange("operation_date", e.target.value)}
          onBlur={() => onBlur("operation_date")}
        />
        {errors.operation_date && (
          <p className="text-xs text-brand-red-dark">{errors.operation_date}</p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={id("care_setting")}>Care setting</Label>
        <select
          id={id("care_setting")}
          value={form.care_setting}
          onChange={(e) => onChange("care_setting", e.target.value)}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">Not given</option>
          <option value="inpatient">Inpatient</option>
          <option value="outpatient">Outpatient</option>
        </select>
        <p className="text-xs text-ink-soft">Picks which clinic guidelines apply when the agent answers questions.</p>
      </div>
      {showCampaign && (
        <div className="space-y-1.5">
          <Label htmlFor={id("campaign_id")}>Use case</Label>
          <select
            id={id("campaign_id")}
            value={campaignValue}
            onChange={(e) => {
              onChange("campaign_id", e.target.value);
              // Which languages can be called depends on the use case.
              if (isAvailable(form.language, e.target.value, langs)) onChange("call_in_english", false);
            }}
            disabled={!campaigns}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">{campaignName(defaultCampaign, campaigns)} (default)</option>
            {/* Until the list loads, only the current value (so it is never lost). */}
            {!campaigns && campaignValue ? <option value={campaignValue}>{campaignValue}</option> : null}
            {(campaigns?.campaigns ?? [])
              .filter((c) => c.id !== defaultCampaign)
              .map((c) => (
                <option key={c.id} value={c.id}>{campaignLabel(c)}</option>
              ))}
          </select>
          <p className="text-xs text-ink-soft">Which call the patient gets: its questions and follow-up. Same as the CSV&apos;s campaign_id column.</p>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor={id("language")}>Preferred language</Label>
        <select
          id={id("language")}
          value={form.language}
          onChange={(e) => {
            onChange("language", e.target.value);
            // The override only means something for a language not available yet.
            if (isAvailable(e.target.value, form.campaign_id, langs)) onChange("call_in_english", false);
          }}
          disabled={!langs}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {/* Until the list loads, only the current value (so it is never lost). */}
          {!langs ? <option value={form.language}>{form.language}</option> : null}
          {list.map((l) => (
            <option key={l.code} value={l.code}>
              {optionLabel(l)}{languageOptionSuffix(l.code, form.campaign_id, langs)}
            </option>
          ))}
        </select>
        {langs && !available ? (
          <div className="space-y-1 rounded-md bg-brand-yellow/20 p-2">
            <div className="flex items-center gap-2">
              <Checkbox
                id={id("call_in_english")}
                checked={form.call_in_english}
                onCheckedChange={(v) => onChange("call_in_english", Boolean(v))}
              />
              <Label htmlFor={id("call_in_english")} className="font-normal cursor-pointer">
                Call in English
              </Label>
            </div>
            <p className="text-xs text-ink-soft">{callInEnglishHint(form.language, list)}</p>
          </div>
        ) : null}
        {errors.language && <p className="text-xs text-brand-red-dark">{errors.language}</p>}
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={id("is_diabetic")}
          checked={form.is_diabetic}
          onCheckedChange={(v) => onChange("is_diabetic", Boolean(v))}
        />
        <Label htmlFor={id("is_diabetic")} className="font-normal cursor-pointer">
          Patient is diabetic
        </Label>
      </div>
    </>
  );
}
