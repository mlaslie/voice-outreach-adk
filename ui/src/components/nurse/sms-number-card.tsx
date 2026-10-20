"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeftRight, FlaskConical, Smartphone } from "lucide-react";
import type { SmsControlAction } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTimeFormat } from "@/lib/time";
import {
  DRY_RUN_NOTE,
  activeName,
  checkedText,
  last4,
  smsButton,
  smsConfirm,
} from "@/lib/sms-control";
import { SmsControlMode, useSmsControl } from "@/hooks/use-sms-control";
import { SmsIndicator } from "@/components/sms-indicator";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Panel } from "./parts";

/**
 * Admin → Messaging → SMS number: which system (this one, or the other one sharing the
 * number) receives incoming texts, and the one button that moves them
 * (docs/sms-number-switch.md). Wording is shared with the other dashboard.
 */
export function SmsNumberCard({ mode }: { mode: SmsControlMode }) {
  useTimeFormat();
  const { status, error: loadError, refresh, flip } = useSmsControl(mode);
  const [confirm, setConfirm] = useState<SmsControlAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [flipError, setFlipError] = useState<string | null>(null);

  // Opening Messaging reads the number now rather than trusting the 30 s cache.
  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  if (status && !status.enabled) {
    return (
      <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
        <Tile />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">SMS number</h3>
          <p className="text-[12px] text-ink-muted">The shared-number switch is turned off on this deployment.</p>
        </div>
      </Panel>
    );
  }

  const button = status ? smsButton(status) : null;
  const dialog = status && confirm ? smsConfirm(status, confirm) : null;
  const errorText = flipError ?? status?.error ?? (loadError ? `Could not read the SMS number: ${loadError}` : null);

  const doFlip = async () => {
    if (!confirm || !button) return;
    const target = button.target;
    setBusy(true);
    setFlipError(null);
    try {
      await flip(confirm);
      if (mode === "live") toast.success(`Incoming texts now go to ${target}`);
    } catch (e) {
      setFlipError(e instanceof Error ? e.message : "Could not change the SMS number.");
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };

  return (
    <Panel className="px-5 py-3.5">
      <div className="flex flex-wrap items-center gap-3">
        <Tile />
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">SMS number</h3>
          <p className="text-[12px] text-ink-muted">
            Incoming texts on the shared number go to one system at a time. Only the active system sends texts.
          </p>
        </div>
        {button ? (
          // A disabled button shows no tooltip; the wrapper carries it.
          <span title={button.reason ?? undefined} className="ml-auto">
            <button
              type="button"
              onClick={() => { setFlipError(null); setConfirm(button.action); }}
              disabled={button.disabled || busy}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-[13px] font-semibold disabled:opacity-40",
                button.action === "take"
                  ? "bg-brand text-white shadow-sm hover:bg-brand-dark"
                  : "bg-white text-brand-deep ring-1 ring-black/[0.08] hover:bg-brand/[0.06]",
              )}
            >
              <ArrowLeftRight className="h-3.5 w-3.5" /> {button.label}
            </button>
          </span>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 pl-12 text-[13px] text-ink-soft">
        {status ? (
          <>
            <span>
              Number ending <span className="font-semibold tabular-nums text-ink">{last4(status)}</span>
            </span>
            <SmsIndicator mode={mode} />
            <span>Incoming texts go to {activeName(status)}.</span>
            <span className="text-ink-muted">{checkedText(status)}</span>
          </>
        ) : loadError ? null : (
          <span className="text-ink-muted">Checking the number…</span>
        )}
      </div>

      {status?.dry_run ? (
        <p className="mt-2 flex items-center gap-2 pl-12 text-[12px] text-ink-soft">
          <FlaskConical className="h-3.5 w-3.5 shrink-0 text-brand-orange" /> {DRY_RUN_NOTE}
        </p>
      ) : null}

      {errorText ? (
        <div role="alert" className="mt-2 ml-12 flex flex-wrap items-center gap-2 rounded-xl bg-brand-red/[0.07] px-3 py-2 text-[12px] font-semibold text-brand-red-dark">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1">{errorText}</span>
          {!flipError ? (
            <button type="button" onClick={() => void refresh(true)}
                    className="rounded-full bg-white px-3 py-1 text-ink ring-1 ring-black/[0.06]">
              Check again
            </button>
          ) : null}
        </div>
      ) : null}

      <Dialog open={!!dialog} onOpenChange={(open) => { if (!open && !busy) setConfirm(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{button?.label}</DialogTitle>
            <DialogDescription>{dialog?.question}</DialogDescription>
          </DialogHeader>
          {dialog?.warning ? (
            <p className="flex items-start gap-2 rounded-xl bg-amber-100 px-3 py-2 text-[13px] text-amber-900 ring-1 ring-inset ring-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {dialog.warning}
            </p>
          ) : null}
          <DialogFooter>
            <button type="button" onClick={() => setConfirm(null)} disabled={busy}
                    className="rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink">
              Cancel
            </button>
            <button type="button" onClick={doFlip} disabled={busy}
                    className="rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-brand-dark disabled:opacity-60">
              {busy ? "Switching…" : button?.label}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

function Tile() {
  return (
    <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand-deep">
      <Smartphone className="h-4 w-4" />
    </div>
  );
}
