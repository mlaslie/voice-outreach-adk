"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Lock, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { api, ApiError } from "@/lib/api";
import { AllowlistEntry } from "@/lib/types";
import { formatPhone } from "@/lib/format";
import {
  CONSENT_STATEMENT,
  CONSENT_VERSION,
  PRIVACY_URL,
  TERMS_URL,
  consentLabel,
} from "@/lib/consent";
import { setSmsConsent } from "@/lib/followups";
import { SUPPORT_EMAIL } from "@/lib/site";

const PHONE_RE = /^\+[1-9]\d{6,14}$/;


interface AllowlistDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Prefills the number — used by "Allow this number" on a blocked row. */
  prefillPhone?: string;
  prefillLabel?: string;
  onChanged?: () => void;
}

export function AllowlistDialog({
  open,
  onOpenChange,
  prefillPhone,
  prefillLabel,
  onChanged,
}: AllowlistDialogProps) {
  const [entries, setEntries] = useState<AllowlistEntry[]>([]);
  const [unrestricted, setUnrestricted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [consented, setConsented] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.getAllowlist();
      setEntries(res.entries);
      setUnrestricted(res.unrestricted);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the allowlist");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setPhone(prefillPhone ?? "");
    setLabel(prefillLabel ?? "");
    setConsented(false);      // never carries over between numbers
    setError(null);
    load();
  }, [open, prefillPhone, prefillLabel, load]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const p = phone.trim();
    if (!PHONE_RE.test(p)) {
      setError("Use E.164 format, e.g. +12025550123");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.addAllowlistEntry(p, consentLabel(label.trim(), consented));
      await setSmsConsent(p, consented, CONSENT_VERSION);
      toast.success(
        consented
          ? `${formatPhone(p)} can now be called and texted`
          : `${formatPhone(p)} can now be called`
      );
      setPhone("");
      setLabel("");
      setConsented(false);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the number");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(p: string) {
    if (!window.confirm(`Stop allowing calls to ${formatPhone(p)}?`)) return;
    setBusy(true);
    try {
      await api.removeAllowlistEntry(p);
      toast.success(`${formatPhone(p)} removed`);
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not remove the number");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Allowed numbers</DialogTitle>
          <DialogDescription>
            Only these destinations can be dialed. Adding a number here authorizes
            real outbound calls to it — no redeploy needed.
          </DialogDescription>
        </DialogHeader>

        {unrestricted && (
          <div className="flex items-start gap-2 rounded-lg border border-brand-orange bg-brand-orange/15 px-3 py-2 text-sm text-ink">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <span>
              <strong>OUTBOUND_ALLOWLIST is set to <code>*</code></strong> — every
              destination is currently permitted and this list is not enforced.
              Remove that setting to make it take effect.
            </span>
          </div>
        )}

        <form onSubmit={handleAdd} className="space-y-3">
          <div className="flex gap-2 items-end">
            <div className="space-y-1.5 flex-1">
              <Label htmlFor="al-phone">Phone number</Label>
              <Input
                id="al-phone"
                inputMode="tel"
                placeholder="+12025550123"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <div className="space-y-1.5 flex-1">
              <Label htmlFor="al-label">Label (optional)</Label>
              <Input
                id="al-label"
                placeholder="e.g. mobile"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>
          </div>

          {/* Optional, and it must stay optional: a required consent box is
              what got the A2P campaign rejected (error 30923). Adding the
              number authorises calls; this authorises texts, separately. */}
          <div className="rounded-lg border border-rule bg-mist/50 p-3">
            <div className="flex items-start gap-2.5">
              <Checkbox
                id="al-consent"
                checked={consented}
                onCheckedChange={(v) => setConsented(v === true)}
                className="mt-0.5"
                aria-describedby="al-consent-text"
              />
              <Label
                htmlFor="al-consent"
                id="al-consent-text"
                className="text-sm font-normal leading-snug text-ink cursor-pointer"
              >
                {CONSENT_STATEMENT}
              </Label>
            </div>
            <p className="mt-2 pl-[26px] text-xs text-ink-muted">
              Optional. Leave it unticked and the number can still be called —
              it just won&apos;t receive follow-up texts. Your answer either way is
              recorded with today&apos;s date. To withdraw later, reply STOP to any
              message or email {SUPPORT_EMAIL}.
            </p>
            {/* Policy links at the point of collection. Compliance checklists
                look for them here, next to the box, not merely somewhere on
                the site. */}
            <p className="mt-1.5 pl-[26px] text-xs text-ink-muted">
              <a href={PRIVACY_URL} target="_blank" rel="noopener noreferrer"
                 className="underline hover:text-ink">SMS privacy policy</a>
              {" · "}
              <a href={TERMS_URL} target="_blank" rel="noopener noreferrer"
                 className="underline hover:text-ink">SMS terms</a>
            </p>
          </div>

          <div className="flex justify-end">
            <Button type="submit" disabled={busy}>
              <Plus className="h-4 w-4 mr-1.5" />
              Allow
            </Button>
          </div>
        </form>

        {error && (
          <p className="text-sm text-brand-red-dark bg-brand-red/10 rounded p-2">{error}</p>
        )}

        <div className="rounded-lg border divide-y">
          {loading ? (
            <div className="p-3 space-y-2">
              <Skeleton className="h-4 w-56" />
              <Skeleton className="h-4 w-40" />
            </div>
          ) : entries.length === 0 ? (
            <p className="p-4 text-sm text-ink-soft text-center">
              No numbers allowed yet. Outbound calling stays disabled until you add one.
            </p>
          ) : (
            entries.map((e) => (
              <div key={`${e.source}-${e.phone}`} className="flex items-center gap-3 px-3 py-2">
                <span className="font-mono text-sm text-ink flex-1">{e.phone}</span>
                <span className="text-xs text-ink-soft flex-1 truncate">{e.label}</span>
                {e.editable ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRemove(e.phone)}
                    disabled={busy}
                    title={`Remove ${e.phone}`}
                    className="text-brand-red-dark hover:bg-brand-red/10"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span className="sr-only">Remove</span>
                  </Button>
                ) : (
                  <span
                    className="inline-flex items-center gap-1 text-xs text-ink-muted px-2"
                    title="Set via the OUTBOUND_ALLOWLIST env var; change it in the deployment"
                  >
                    <Lock className="h-3 w-3" />
                    env
                  </span>
                )}
              </div>
            ))
          )}
        </div>

        <p className="text-xs text-ink-soft">
          Adding and removing is authorised by the backend against your signed-in
          account, and every change is recorded with who made it.
        </p>
      </DialogContent>
    </Dialog>
  );
}
