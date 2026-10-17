"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
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
import { api, ApiError } from "@/lib/api";
import { CallListEntry } from "@/lib/types";

interface CallListEntryDialogProps {
  open: boolean;
  /** null = add a new patient; otherwise edit this entry. */
  entry: CallListEntry | null;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

function entryToForm(entry: CallListEntry): PatientFormState {
  return {
    phone: entry.phone,
    patient_name: entry.patient_name,
    patient_id: entry.patient_id,
    patient_dob: entry.patient_dob,
    operation_name: entry.operation_name ?? "",
    operation_date: entry.operation_date ?? "",
    is_diabetic: !!entry.is_diabetic,
    scheduled_for: isoToLocalInput(entry.scheduled_for),
  };
}

export function CallListEntryDialog({
  open,
  entry,
  onOpenChange,
  onSaved,
}: CallListEntryDialogProps) {
  const [form, setForm] = useState<PatientFormState>(INITIAL_PATIENT_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // Reload the form whenever the dialog opens on a different entry.
  useEffect(() => {
    if (!open) return;
    setForm(entry ? entryToForm(entry) : INITIAL_PATIENT_FORM);
    setErrors({});
    setServerError(null);
  }, [open, entry]);

  function set(key: keyof PatientFormState, value: string | boolean) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function handleBlur(key: keyof PatientFormState) {
    const fieldErrors = validatePatientForm(form);
    if (fieldErrors[key]) {
      setErrors((prev) => ({ ...prev, [key]: fieldErrors[key] }));
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errs = validatePatientForm(form);
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    setSubmitting(true);
    setServerError(null);
    try {
      const body = entryFormToRequest(form);
      if (entry) {
        await api.updateCallListEntry(entry.entry_id, body);
        toast.success(`Updated ${body.patient_name}`);
      } else {
        await api.createCallListEntry(body);
        toast.success(`Added ${body.patient_name} to the call list`);
      }
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setServerError(err instanceof ApiError ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{entry ? "Edit patient" : "Add patient to call list"}</DialogTitle>
          <DialogDescription>
            {entry
              ? "Changes apply to the next run. They do not affect calls already placed."
              : "The patient is added as pending and will be dialed on the next run."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <PatientFields
            form={form}
            errors={errors}
            idPrefix="cl-"
            onChange={set}
            onBlur={handleBlur}
          />
          {serverError && (
            <p className="text-sm text-brand-red-dark bg-brand-red/10 rounded p-2">{serverError}</p>
          )}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Saving…" : entry ? "Save changes" : "Add patient"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
