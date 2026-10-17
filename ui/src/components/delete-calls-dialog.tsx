"use client";

import { useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
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
import { api, ApiError } from "@/lib/api";

interface DeleteCallsDialogProps {
  open: boolean;
  selectedSids: string[];
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}

export function DeleteCallsDialog({
  open,
  selectedSids,
  onOpenChange,
  onDeleted,
}: DeleteCallsDialogProps) {
  const [busy, setBusy] = useState(false);
  const count = selectedSids.length;

  async function confirm() {
    if (count === 0) return;
    setBusy(true);
    try {
      const res = await api.batchDelete(selectedSids);
      const deleted = res.results.filter((r) => r.status === "deleted").length;
      const skipped = res.results.length - deleted;
      const gcs = res.results.reduce(
        (n, r) => n + (r.removed?.gcs_objects ?? 0),
        0,
      );
      toast.success(
        `Deleted ${deleted} call${deleted === 1 ? "" : "s"}` +
          (gcs > 0 ? ` and ${gcs} recording file${gcs === 1 ? "" : "s"}` : "") +
          (skipped > 0 ? ` (${skipped} skipped)` : ""),
      );
      onDeleted();
      onOpenChange(false);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Unknown error deleting calls.";
      toast.error(`Delete failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !busy && onOpenChange(v)}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-brand-red" />
            Delete {count} call{count === 1 ? "" : "s"}?
          </DialogTitle>
          <DialogDescription>
            This will permanently remove the call record, the associated
            post-op report, the call log, and the GCS recording files
            (full-session plus every per-turn audio file). This cannot be
            undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            className="bg-brand-red text-white hover:bg-brand-red-dark"
            onClick={confirm}
            disabled={busy}
          >
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Delete {count}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
