"use client";

import { useState } from "react";
import { toast } from "sonner";
import { FlaskConical, ShieldAlert } from "lucide-react";
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
  patientFormToRequest,
  validatePatientForm,
} from "@/components/patient-fields";
import { NurseActions } from "@/lib/nurse/actions";
import { isAllowed } from "@/lib/nurse/allowlist";
import { AllowlistResponse } from "@/lib/types";

const NOT_AUTHORIZED = "Not an authorized number. An admin adds it under Authorized numbers in the admin console.";

/**
 * Nurse → Call queue → New call: call one patient now with details typed in,
 * for someone who is not on the call list (the classic dashboard's "Place a
 * Call", moved here). The number must be on the authorized list; the API
 * refuses it otherwise, and the console says so before dialling when it can.
 */
export function NewCallDialog({
  open,
  onOpenChange,
  actions,
  allowlist,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  actions: Pick<NurseActions, "live" | "placeCall">;
  /** The authorized numbers (live only), to warn before dialling. */
  allowlist?: AllowlistResponse | null;
}) {
  const [form, setForm] = useState<PatientFormState>(INITIAL_PATIENT_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const validate = (f: PatientFormState) => {
    const e = validatePatientForm(f);
    delete e.scheduled_for; // not asked here: the call starts now
    if (!e.phone && actions.live && isAllowed(f.phone.trim(), allowlist) === false) e.phone = NOT_AUTHORIZED;
    return e;
  };

  const set = (key: keyof PatientFormState, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const blur = (key: keyof PatientFormState) => {
    const e = validate(form);
    if (e[key]) setErrors((prev) => ({ ...prev, [key]: e[key] }));
  };

  const close = (o: boolean) => {
    if (!o) {
      setForm(INITIAL_PATIENT_FORM);
      setErrors({});
      setServerError(null);
    }
    onOpenChange(o);
  };

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const e = validate(form);
    if (Object.keys(e).length) {
      setErrors(e);
      return;
    }
    setSubmitting(true);
    setServerError(null);
    try {
      const body = patientFormToRequest(form);
      const sid = await actions.placeCall(body);
      if (sid) toast.success(`Calling ${body.patient_name}. The call shows under Call status when it ends.`);
      close(false);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Could not place the call.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display">New call</DialogTitle>
          <DialogDescription>
            Call one patient now, with details you type in. For a patient already on the queue, use their Call now button instead.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4 py-2">
          {actions.live ? null : (
            <p className="flex items-start gap-2 rounded-xl bg-brand-yellow/30 p-2.5 text-[13px] text-ink ring-1 ring-brand-yellow/60">
              <FlaskConical className="mt-0.5 h-4 w-4 shrink-0" />
              Sample data: nothing is sent and nobody is called. The form checks the details only.
            </p>
          )}
          <PatientFields form={form} errors={errors} idPrefix="new-call-" onChange={set} onBlur={blur} />
          <p className="flex items-start gap-2 rounded-xl bg-brand-orange/15 p-2.5 text-[12px] text-ink">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            The agent dials this number as soon as you press Call. Only authorized numbers can be called.
          </p>
          {serverError ? <p className="rounded-xl bg-brand-red/10 p-2 text-sm text-brand-red-dark">{serverError}</p> : null}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" disabled={submitting}>{submitting ? "Dialling…" : "Call"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
