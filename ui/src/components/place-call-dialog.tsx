"use client";

import { useState } from "react";
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
  patientFormToRequest,
  validatePatientForm,
} from "@/components/patient-fields";
import { api, ApiError } from "@/lib/api";

interface PlaceCallDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPlaced: (callSid: string) => void;
}

export function PlaceCallDialog({ open, onOpenChange, onPlaced }: PlaceCallDialogProps) {
  const [form, setForm] = useState<PatientFormState>(INITIAL_PATIENT_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

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
      const res = await api.placeCall(patientFormToRequest(form));
      toast.success(`Call queued (${res.call_sid})`);
      setForm(INITIAL_PATIENT_FORM);
      setErrors({});
      onPlaced(res.call_sid);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Unexpected error";
      setServerError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  function handleClose(open: boolean) {
    if (!open) {
      setForm(INITIAL_PATIENT_FORM);
      setErrors({});
      setServerError(null);
    }
    onOpenChange(open);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Place a Call</DialogTitle>
          <DialogDescription>
            Starts a live outbound call from the Care Team agent to the number below.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <PatientFields
            form={form}
            errors={errors}
            idPrefix="pc-"
            onChange={set}
            onBlur={handleBlur}
          />
          <p className="text-xs text-ink bg-brand-orange/15 rounded p-2">
            Demo only — use a test phone you control.
          </p>
          {serverError && (
            <p className="text-sm text-brand-red-dark bg-brand-red/10 rounded p-2">{serverError}</p>
          )}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Dialing…" : "Place call"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
