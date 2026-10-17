"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  INITIAL_PATIENT_FORM,
  PatientFields,
  PatientFormState,
  entryFormToRequest,
  isoToLocalInput,
  validatePatientForm,
} from "@/components/patient-fields";
import { CallListEntry } from "@/lib/types";
import { NurseActions } from "@/lib/nurse/actions";

/**
 * Add / edit a call-list patient from the nurse console. Same fields as the
 * classic dashboard's dialog plus the planned call time, but it saves
 * through NurseActions so sample data stays sample data.
 */
export function EntryDialog({
  open,
  entry,
  actions,
  onOpenChange,
}: {
  open: boolean;
  entry: CallListEntry | null;
  actions: NurseActions;
  onOpenChange: (open: boolean) => void;
}) {
  const [form, setForm] = useState<PatientFormState>(INITIAL_PATIENT_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setForm(
      entry
        ? {
            phone: entry.phone,
            patient_name: entry.patient_name,
            patient_id: entry.patient_id,
            patient_dob: entry.patient_dob,
            operation_name: entry.operation_name ?? "",
            operation_date: entry.operation_date ?? "",
            is_diabetic: !!entry.is_diabetic,
            scheduled_for: isoToLocalInput(entry.scheduled_for),
          }
        : INITIAL_PATIENT_FORM,
    );
    setErrors({});
    setServerError(null);
  }, [open, entry]);

  const set = (key: keyof PatientFormState, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const blur = (key: keyof PatientFormState) => {
    const e = validatePatientForm(form);
    if (e[key]) setErrors((prev) => ({ ...prev, [key]: e[key] }));
  };

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const e = validatePatientForm(form);
    if (Object.keys(e).length) {
      setErrors(e);
      return;
    }
    setSubmitting(true);
    setServerError(null);
    try {
      const body = entryFormToRequest(form);
      await actions.saveEntry(entry, body);
      if (actions.live) toast.success(entry ? `Updated ${body.patient_name}` : `Added ${body.patient_name}`);
      onOpenChange(false);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display">{entry ? "Edit patient" : "Add a patient to call"}</DialogTitle>
          <DialogDescription>
            {entry
              ? "Changes apply to the next call. Calls already placed are unaffected."
              : "Everything the agent needs to verify the patient and ask the right questions."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4 py-2">
          <PatientFields form={form} errors={errors} idPrefix="nurse-" onChange={set} onBlur={blur} />
          {serverError ? <p className="rounded-xl bg-brand-red/10 p-2 text-sm text-brand-red-dark">{serverError}</p> : null}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={submitting}>{submitting ? "Saving…" : entry ? "Save changes" : "Add patient"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
