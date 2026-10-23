"use client";

import { useState } from "react";
import { CheckCircle2, FileWarning, Upload, XCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { AdminActions } from "@/lib/nurse/actions";
import { BULK_LIMIT, CsvPreview, ImportResult } from "@/lib/call-list-csv";
import { formatPhone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CampaignsResponse, campaignName } from "@/lib/campaigns";
import { CsvColumnsHelp } from "./csv-columns-help";

/**
 * Admin → Patients → Import CSV: the parsed file, row by row, with every
 * problem on each row, before anything is sent. Only the rows without errors
 * are imported, in requests of at most 200 (the backend's limit); the summary
 * then says how many were added and how many were rejected, and why.
 */
export function ImportDialog({
  fileName,
  preview,
  actions,
  campaigns = null,
  onClose,
}: {
  fileName: string;
  /** null closes the dialog. */
  preview: CsvPreview | null;
  actions: Pick<AdminActions, "live" | "importEntries">;
  /** GET /api/campaigns, to name each row's use case. */
  campaigns?: CampaignsResponse | null;
  onClose: () => void;
}) {
  const [helpOpen, setHelpOpen] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const sending = progress !== null && result === null;

  const close = () => {
    if (sending) return; // the requests are already on their way
    setProgress(null);
    setResult(null);
    setHelpOpen(false);
    onClose();
  };

  const run = async () => {
    if (!preview?.valid.length) return;
    setProgress({ done: 0, total: preview.valid.length });
    const r = await actions.importEntries(preview.valid, (done, total) => setProgress({ done, total }));
    setResult(r);
  };

  const valid = preview?.valid.length ?? 0;
  const invalid = preview?.invalid ?? 0;
  const serverRejected = result?.rejected.reduce((n, r) => n + r.count, 0) ?? 0;

  return (
    <Dialog open={!!preview} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-[24px] sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-display">{result ? "Import finished" : "Import patients"}</DialogTitle>
          <DialogDescription>
            {fileName} · {preview?.rows.length ?? 0} {preview?.rows.length === 1 ? "row" : "rows"}.
            {result ? "" : " Nothing has been sent yet."}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3 py-1">
            <div className="grid grid-cols-2 gap-3">
              <Stat tone="good" value={result.added} label={result.added === 1 ? "patient added" : "patients added"} />
              <Stat tone={invalid + serverRejected ? "bad" : "muted"} value={invalid + serverRejected} label="rejected" />
            </div>
            {invalid ? (
              <p className="text-[13px] text-ink-soft">
                {invalid} {invalid === 1 ? "row had" : "rows had"} errors in the file and {invalid === 1 ? "was" : "were"} not sent.
                Fix {invalid === 1 ? "it" : "them"} and import just those rows again.
              </p>
            ) : null}
            {result.rejected.map((r, i) => (
              <p key={i} className="rounded-xl bg-brand-red/10 px-3 py-2 text-[13px] text-brand-red-dark">
                The backend refused {r.count} {r.count === 1 ? "row" : "rows"}: {r.reason}
              </p>
            ))}
            {!actions.live ? (
              <p className="text-[12px] text-ink-muted">Sample data: the patients were added to this tab only. Nothing was sent.</p>
            ) : null}
          </div>
        ) : preview ? (
          <div className="min-h-0 space-y-3">
            <div className="flex flex-wrap gap-2 text-[13px]">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-brand/10 px-3 py-1 font-semibold text-brand-dark">
                <CheckCircle2 className="h-3.5 w-3.5" /> {valid} ready
              </span>
              {invalid ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-red/10 px-3 py-1 font-semibold text-brand-red-dark">
                  <XCircle className="h-3.5 w-3.5" /> {invalid} with errors, skipped
                </span>
              ) : null}
              {valid > BULK_LIMIT ? (
                <span className="rounded-full bg-mist px-3 py-1 text-ink-soft">
                  Sent in {Math.ceil(valid / BULK_LIMIT)} batches of up to {BULK_LIMIT}
                </span>
              ) : null}
            </div>
            <div className="max-h-[45vh] overflow-auto rounded-2xl ring-1 ring-black/[0.06]">
              <table className="w-full text-left text-[13px]">
                <thead className="sticky top-0 bg-mist text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
                  <tr>
                    <th className="px-3 py-2">Row</th>
                    <th className="px-3 py-2">Patient</th>
                    <th className="px-3 py-2">Phone</th>
                    <th className="px-3 py-2">DOB</th>
                    <th className="px-3 py-2">Use case</th>
                    <th className="px-3 py-2">Check</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-black/[0.05] bg-white">
                  {preview.rows.map((r) => {
                    const bad = r.errors.length > 0;
                    return (
                      <tr key={r.row} className={cn("align-top", bad && "bg-brand-red/[0.04]")}>
                        <td className="px-3 py-2 tabular-nums text-ink-muted">{r.row}</td>
                        <td className="px-3 py-2">
                          <div className="font-semibold text-ink">{r.cells.patient_name || "—"}</div>
                          <div className="text-[12px] text-ink-muted">{r.cells.patient_id || "—"}</div>
                        </td>
                        <td className="px-3 py-2 tabular-nums text-ink-soft">
                          {/^\+[1-9]\d{6,14}$/.test(r.cells.phone ?? "") ? formatPhone(r.cells.phone) : r.cells.phone || "—"}
                        </td>
                        <td className="px-3 py-2 tabular-nums text-ink-soft">{r.cells.patient_dob || "—"}</td>
                        <td className="px-3 py-2 text-ink-soft">
                          {r.entry
                            ? <>{campaignName(r.entry.campaign_id, campaigns)}{r.entry.campaign_id ? null : <span className="text-ink-muted"> (default)</span>}</>
                            : r.cells.campaign_id || <span className="text-ink-muted">default</span>}
                        </td>
                        <td className="px-3 py-2">
                          {bad ? (
                            <ul className="space-y-0.5 text-[12px] text-brand-red-dark">
                              {r.errors.map((e) => <li key={e}>{e}</li>)}
                            </ul>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[12px] font-semibold text-brand-dark">
                              <CheckCircle2 className="h-3.5 w-3.5" /> Ready
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {invalid ? (
              <p className="flex items-start gap-2 text-[12px] text-ink-soft">
                <FileWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Rows with errors are skipped. Import the ready rows now, or cancel, fix the file and choose it again.
              </p>
            ) : null}
            <p className="text-[12px] text-ink-soft">
              Importing adds the patients to the list. It does not start any calls: start or schedule a run under Schedules.
            </p>
            {!actions.live ? (
              <p className="text-[12px] text-ink-muted">Sample data: importing adds the rows to this tab only. Nothing is sent.</p>
            ) : null}
            <CsvColumnsHelp open={helpOpen} onOpenChange={setHelpOpen} className="rounded-2xl bg-mist/50 px-3 py-2.5" />
          </div>
        ) : null}

        <DialogFooter className="gap-2 sm:gap-0">
          {result ? (
            <Button type="button" onClick={close}>Done</Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={close} disabled={sending}>Cancel</Button>
              <Button type="button" onClick={run} disabled={!valid || sending}>
                <Upload className="mr-1.5 h-3.5 w-3.5" />
                {sending && progress
                  ? `Sending ${progress.done} of ${progress.total}…`
                  : `Import ${valid} ${valid === 1 ? "patient" : "patients"}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone: "good" | "bad" | "muted" }) {
  return (
    <div className={cn("rounded-2xl px-4 py-3 ring-1",
      tone === "good" ? "bg-brand/[0.06] ring-brand/20" : tone === "bad" ? "bg-brand-red/[0.06] ring-brand-red/25" : "bg-mist/60 ring-black/[0.05]")}>
      <div className={cn("font-display text-[28px] font-semibold leading-none tabular-nums",
        tone === "good" ? "text-brand-dark" : tone === "bad" ? "text-brand-red-dark" : "text-ink-soft")}>{value}</div>
      <div className="mt-1 text-[12px] text-ink-soft">{label}</div>
    </div>
  );
}
