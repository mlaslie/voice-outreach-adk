"use client";

import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Lock, MessageSquareOff, MessageSquareText, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { AllowlistResponse } from "@/lib/types";
import { AdminActions } from "@/lib/nurse/actions";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CONSENT_STATEMENT, PRIVACY_URL, TERMS_URL } from "@/lib/consent";
import { SUPPORT_EMAIL } from "@/lib/site";
import { EmptyState, Panel, Pill } from "@/components/nurse/parts";

const PHONE_RE = /^\+[1-9]\d{6,14}$/;

/** A number's text consent: true / false once read, undefined while unknown. */
export type ConsentMap = Record<string, boolean | undefined>;

const INPUT = "h-10 w-full rounded-xl bg-white px-3.5 text-[14px] text-ink ring-1 ring-black/[0.08] placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-brand/40";

/**
 * Admin → Authorized numbers: the outbound dial allowlist and each number's
 * SMS consent. Ported from the classic dashboard's dialog into a page.
 *
 * Two separate permissions (lib/consent.ts): adding a number authorises
 * CALLS; the optional checkbox authorises TEXTS. The consent box must stay
 * optional — a required one is what got the A2P campaign rejected (30923).
 */
export function AllowlistPanel({
  allowlist,
  consent,
  actions,
  error,
}: {
  allowlist: AllowlistResponse | null;
  consent: ConsentMap;
  actions: AdminActions;
  /** The last read failed. */
  error?: string | null;
}) {
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [consented, setConsented] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const p = phone.trim();
    if (!PHONE_RE.test(p)) {
      setFormError("Use E.164 format, e.g. +12025550123");
      return;
    }
    setBusy("add");
    setFormError(null);
    try {
      await actions.addNumber(p, label.trim(), consented);
      if (actions.live) {
        toast.success(consented ? `${formatPhone(p)} can now be called and texted` : `${formatPhone(p)} can now be called`);
      }
      setPhone("");
      setLabel("");
      setConsented(false); // never carries over between numbers
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not add the number");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (p: string) => {
    if (!window.confirm(`Stop allowing calls to ${formatPhone(p)}?`)) return;
    setBusy(p);
    try {
      await actions.removeNumber(p);
      if (actions.live) toast.success(`${formatPhone(p)} removed`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove the number");
    } finally {
      setBusy(null);
    }
  };

  const changeConsent = async (p: string, granted: boolean) => {
    const ask = granted
      ? `Record that ${formatPhone(p)} agreed to receive texts?\n\n${CONSENT_STATEMENT}`
      : `Withdraw text consent for ${formatPhone(p)}? Follow-up texts stop; calls are not affected.`;
    if (!window.confirm(ask)) return;
    setBusy(p);
    try {
      await actions.setConsent(p, granted);
      if (actions.live) toast.success(granted ? `${formatPhone(p)} can be texted` : `Texts to ${formatPhone(p)} stopped`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not record the change");
    } finally {
      setBusy(null);
    }
  };

  const entries = allowlist?.entries ?? [];

  return (
    <div className="space-y-5">
      {allowlist?.unrestricted ? (
        <div role="alert" className="flex items-start gap-3 rounded-2xl bg-brand-orange/15 px-4 py-3 text-[13px] text-ink ring-1 ring-brand-orange/50">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <strong>OUTBOUND_ALLOWLIST is set to <code>*</code></strong>: every destination is currently permitted and
            this list is not enforced. Remove that setting to make it take effect.
          </span>
        </div>
      ) : null}

      <Panel className="p-5">
        <h3 className="font-display text-[15px] font-semibold text-ink">Add a number</h3>
        <p className="text-[12px] text-ink-muted">
          Adding a number authorises real outbound calls to it, with no redeploy.
        </p>
        <form onSubmit={add} className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="al-phone">Phone number</Label>
              <input id="al-phone" inputMode="tel" placeholder="+12025550123" value={phone}
                     onChange={(e) => setPhone(e.target.value)} className={INPUT} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="al-label">Label (optional)</Label>
              <input id="al-label" placeholder="e.g. test phone" value={label}
                     onChange={(e) => setLabel(e.target.value)} className={INPUT} />
            </div>
          </div>

          <div className="rounded-2xl bg-mist/60 p-4 ring-1 ring-inset ring-black/[0.05]">
            <div className="flex items-start gap-2.5">
              <Checkbox id="al-consent" checked={consented} onCheckedChange={(v) => setConsented(v === true)}
                        className="mt-0.5" aria-describedby="al-consent-text" />
              <Label htmlFor="al-consent" id="al-consent-text" className="cursor-pointer text-[13px] font-normal leading-snug text-ink">
                {CONSENT_STATEMENT}
              </Label>
            </div>
            <p className="mt-2 pl-[26px] text-[12px] text-ink-muted">
              Optional. Leave it unticked and the number can still be called; it just won&apos;t receive follow-up
              texts. The answer either way is recorded with today&apos;s date. To withdraw later, reply STOP to any
              message or email {SUPPORT_EMAIL}.
            </p>
            <p className="mt-1.5 pl-[26px] text-[12px] text-ink-muted">
              <a href={PRIVACY_URL} target="_blank" rel="noopener noreferrer" className="underline hover:text-ink">SMS privacy policy</a>
              {" · "}
              <a href={TERMS_URL} target="_blank" rel="noopener noreferrer" className="underline hover:text-ink">SMS terms</a>
            </p>
          </div>

          {formError ? (
            <p className="rounded-xl bg-brand-red/10 px-3 py-2 text-[13px] text-brand-red-dark">{formError}</p>
          ) : null}

          <div className="flex justify-end">
            <button type="submit" disabled={busy !== null}
                    className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-brand-dark disabled:opacity-40">
              <Plus className="h-3.5 w-3.5" /> Allow
            </button>
          </div>
        </form>
      </Panel>

      <Panel className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-black/[0.05] px-5 py-3.5">
          <ShieldCheck className="h-4 w-4 text-brand" />
          <h3 className="font-display text-[15px] font-semibold text-ink">Authorized numbers</h3>
          {allowlist ? <span className="text-[12px] text-ink-muted">{allowlist.count} on the list</span> : null}
        </div>
        {error ? (
          <p className="border-b border-black/[0.05] bg-brand-red/10 px-5 py-3 text-[13px] text-brand-red-dark">{error}</p>
        ) : null}
        {!allowlist ? (
          <p className="px-5 py-6 text-[13px] text-ink-muted">{error ? "Could not load the list." : "Loading…"}</p>
        ) : entries.length === 0 ? (
          <EmptyState title="No numbers allowed yet" hint="Outbound calling stays disabled until you add one." />
        ) : (
          <ul className="divide-y divide-black/[0.05]">
            {entries.map((e) => {
              const c = consent[e.phone];
              return (
                <li key={`${e.source}-${e.phone}`}
                    className={cn("flex flex-wrap items-center gap-3 px-5 py-3", busy === e.phone && "opacity-50")}>
                  <div className="min-w-0 flex-1">
                    <div className="font-display text-[14px] font-semibold tabular-nums text-ink">{formatPhone(e.phone)}</div>
                    <div className="truncate text-[12px] text-ink-muted">{e.label || "No label"}</div>
                  </div>
                  {c === true ? <Pill tone="good" icon={MessageSquareText}>Texts agreed</Pill>
                    : c === false ? <Pill tone="muted" icon={MessageSquareOff}>No text consent</Pill>
                    : <Pill tone="muted">Text consent unknown</Pill>}
                  {e.editable ? (
                    <div className="flex gap-1">
                      {c !== undefined ? (
                        <button
                          onClick={() => changeConsent(e.phone, !c)}
                          disabled={busy !== null}
                          className="rounded-full px-3 py-1.5 text-[12px] font-semibold text-brand-dark ring-1 ring-black/[0.08] hover:bg-brand/[0.06] disabled:opacity-40"
                        >
                          {c ? "Withdraw texts" : "Record consent"}
                        </button>
                      ) : null}
                      <button
                        onClick={() => remove(e.phone)}
                        disabled={busy !== null}
                        aria-label={`Remove ${e.phone}`}
                        className="grid h-8 w-8 place-items-center rounded-lg text-ink-soft hover:bg-brand-red/[0.08] hover:text-brand-red-dark disabled:opacity-40"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 text-[12px] text-ink-muted"
                          title="Set via the OUTBOUND_ALLOWLIST env var; change it in the deployment">
                      <Lock className="h-3 w-3" /> env
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <p className="px-1 text-[12px] text-ink-soft">
        Adding and removing is authorised by the backend against your signed-in account, and every change is recorded
        with who made it.
      </p>
    </div>
  );
}
