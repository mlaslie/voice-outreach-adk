"use client";

import { forwardRef } from "react";
import { ChevronRight, TableProperties } from "lucide-react";
import { CSV_COLUMN_INFO } from "@/lib/call-list-csv";
import { useCampaigns } from "@/components/campaigns-provider";
import { cn } from "@/lib/utils";

const REQUIRED = CSV_COLUMN_INFO.filter((c) => c.required).map((c) => c.name);
const OPTIONAL = CSV_COLUMN_INFO.length - REQUIRED.length;

/**
 * The "CSV columns" help (Admin → Patients, and the import dialog): every
 * column the import reads, from the one list in lib/call-list-csv.ts, plus
 * the rules. The campaign_id values come from GET /api/campaigns.
 */
export const CsvColumnsHelp = forwardRef<
  HTMLDetailsElement,
  {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Highlight these columns (the ones a file was missing). */
    missing?: string[];
    className?: string;
  }
>(function CsvColumnsHelp({ open, onOpenChange, missing = [], className }, ref) {
  const campaigns = useCampaigns();
  return (
    <details
      ref={ref}
      id="csv-columns"
      open={open}
      onToggle={(e) => onOpenChange((e.currentTarget as HTMLDetailsElement).open)}
      className={cn("group text-[13px] text-ink-soft", className)}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
        <TableProperties className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="font-display text-[14px] font-semibold text-ink">CSV columns</span>
        <span className="text-[12px] text-ink-muted">
          {REQUIRED.length} required ({REQUIRED.join(", ")}) · {OPTIONAL} optional
        </span>
      </summary>

      <div className="mt-3 overflow-x-auto rounded-2xl ring-1 ring-black/[0.06]">
        <table className="w-full text-left text-[12px]">
          <thead className="bg-mist text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
            <tr>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Required</th>
              <th className="px-3 py-2">Format</th>
              <th className="px-3 py-2">Example</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-black/[0.05] bg-white">
            {CSV_COLUMN_INFO.map((c) => (
              <tr key={c.name} className={cn("align-top", missing.includes(c.name) && "bg-brand-red/[0.06]")}>
                <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[12px] text-ink">{c.name}</td>
                <td className="px-3 py-1.5">
                  {c.required ? (
                    <span className={cn("font-semibold", missing.includes(c.name) ? "text-brand-red-dark" : "text-ink")}>
                      {missing.includes(c.name) ? "Required, missing" : "Required"}
                    </span>
                  ) : (
                    <span className="text-ink-muted">Optional</span>
                  )}
                </td>
                <td className="px-3 py-1.5">
                  {c.format}
                  {c.name === "campaign_id" ? (
                    <span className="block text-ink-muted">
                      {campaigns
                        ? `Valid: ${campaigns.campaigns.map((x) => `${x.id} (${x.name}${x.status === "active" ? "" : `, ${x.status.replace("_", " ")}`})`).join("; ")}`
                        : "The list of use cases could not be loaded."}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[12px] text-ink-soft">{c.example}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="mt-3 list-disc space-y-1 pl-5 text-[12px]">
        <li>The first row is the header. It must contain the {REQUIRED.length} required columns; the others may be left out, and the order does not matter.</li>
        <li>Columns not listed here are ignored.</li>
        <li>Every row is checked before anything is sent. A row with errors is skipped; the other rows still import.</li>
        <li>Importing adds patients to the list. It does not start any calls: start or schedule a run under Schedules.</li>
      </ul>
    </details>
  );
});
