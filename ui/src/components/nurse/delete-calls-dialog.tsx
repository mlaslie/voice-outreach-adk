"use client";

import { useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
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
import { NurseActions } from "@/lib/nurse/actions";
import { DeletePlan, confirmText, removedIds, summariseResults } from "@/lib/nurse/delete-calls";

/**
 * Confirm and run a multi-select delete from Call status. Rows leave the
 * board chunk by chunk as the backend confirms them (`onRemoved`); the toast
 * shows progress, then the outcome. In sample mode the action removes them
 * from this tab's data and says nothing is sent.
 */
export function DeleteCallsDialog({
  plan,
  actions,
  onOpenChange,
  onRemoved,
}: {
  /** null = closed. */
  plan: DeletePlan | null;
  actions: NurseActions;
  onOpenChange: (open: boolean) => void;
  onRemoved: (callIds: string[]) => void;
}) {
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const busy = progress !== null;
  const text = plan ? confirmText(plan) : null;
  const n = plan?.callIds.length ?? 0;

  async function confirm() {
    if (!plan || !n || busy) return;
    const total = n;
    setProgress({ done: 0, total });
    const toastId = actions.live ? toast.loading(`Deleting ${total === 1 ? "1 call" : `${total} calls`}…`) : undefined;
    try {
      const results = await actions.deleteCalls(plan.callIds, (chunkResults, done) => {
        onRemoved(removedIds(chunkResults));
        setProgress({ done, total });
        if (toastId !== undefined && done < total) toast.loading(`Deleting calls… ${done} of ${total}`, { id: toastId });
      });
      if (toastId !== undefined) {
        const s = summariseResults(results);
        if (s.failed === 0) toast.success(s.message, { id: toastId });
        else if (s.deleted + s.alreadyGone > 0) toast.warning(s.message, { id: toastId });
        else toast.error(`${s.failed} could not be deleted`, { id: toastId });
      }
      onOpenChange(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not delete the calls.";
      if (toastId !== undefined) toast.error(msg, { id: toastId });
      else toast.error(msg);
    } finally {
      setProgress(null);
    }
  }

  return (
    <Dialog open={!!plan} onOpenChange={(v) => !busy && onOpenChange(v)}>
      <DialogContent className="sm:max-w-[460px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-brand-red" aria-hidden />
            {text?.title}
          </DialogTitle>
          <DialogDescription>{text?.body}</DialogDescription>
        </DialogHeader>
        {text?.skipped.length ? (
          <ul className="space-y-1 rounded-xl bg-mist px-4 py-3 text-[13px] text-ink-soft">
            {text.skipped.map((s) => <li key={s}>{s}</li>)}
          </ul>
        ) : null}
        {progress ? (
          <div role="status" aria-live="polite" className="space-y-1.5">
            <div className="text-[12px] text-ink-muted">Deleting… {progress.done} of {progress.total}</div>
            <div className="h-1.5 overflow-hidden rounded-full bg-mist">
              <div className="h-full bg-brand-red transition-all"
                   style={{ width: `${Math.round((progress.done / Math.max(progress.total, 1)) * 100)}%` }} />
            </div>
          </div>
        ) : null}
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            {n ? "Cancel" : "Close"}
          </Button>
          {n ? (
            <Button variant="destructive" onClick={confirm} disabled={busy}>
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
              {busy ? "Deleting…" : `Delete ${n === 1 ? "1 call" : `${n} calls`}`}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
