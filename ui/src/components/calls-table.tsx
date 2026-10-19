"use client";

import { useState } from "react";
import { PhoneOutgoing, Trash2, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { StatusChip } from "./status-chip";
import { OutcomeChip } from "./outcome-chip";
import { DeleteCallsDialog } from "./delete-calls-dialog";
import { CallDoc } from "@/lib/types";
import { QuestionCountPill } from "./patient-questions";
import { reasonTierLabel, reasonsFor } from "@/lib/follow-up-reasons";
import { formatPhone, formatDuration, relativeTime } from "@/lib/format";
import {
  TERMINAL,
  STATUS_LABELS,
  getFollowUpChipClasses,
  getFollowUpLabel,
} from "@/lib/status";

interface CallsTableProps {
  calls: CallDoc[];
  loading: boolean;
  filter: string;
  sortDir: "desc" | "asc";
  /** Table is filtered to calls that need a human callback. */
  followUpOnly?: boolean;
  onFilterChange: (v: string) => void;
  onSortChange: (v: "desc" | "asc") => void;
  onFollowUpOnlyChange?: (v: boolean) => void;
  onSelect: (sid: string) => void;
  onDeleted?: () => void;
}

const DOB_LABEL: Record<string, string> = {
  verified: "✓ verified",
  mismatch: "✗ mismatch",
  refused: "refused",
  skipped: "skipped",
};

export function CallsTable({
  calls,
  loading,
  filter,
  sortDir,
  followUpOnly = false,
  onFilterChange,
  onSortChange,
  onFollowUpOnlyChange,
  onSelect,
  onDeleted,
}: CallsTableProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Only local_* / CA... rows that map to real backend docs can be
  // deleted. Every call in this table has doc_id === its Firestore key.
  const selectableIds = calls.map((c) => c.doc_id);
  const allSelected =
    selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
  const someSelected = selected.size > 0 && !allSelected;

  function toggleAll(checked: boolean) {
    if (checked) setSelected(new Set(selectableIds));
    else setSelected(new Set());
  }
  function toggleOne(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function afterDeleted() {
    setSelected(new Set());
    onDeleted?.();
  }

  if (loading) {
    return (
      <div className="rounded-lg border bg-white p-4 space-y-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex gap-4">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-12" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-white">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-3">
          <Select value={filter} onValueChange={onFilterChange}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Status: All" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              {[...TERMINAL].map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_LABELS[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sortDir} onValueChange={(v) => onSortChange(v as "desc" | "asc")}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">Newest first</SelectItem>
              <SelectItem value="asc">Oldest first</SelectItem>
            </SelectContent>
          </Select>
          {followUpOnly && (
            <button
              onClick={() => onFollowUpOnlyChange?.(false)}
              className="inline-flex items-center gap-1.5 rounded-full border border-brand-orange bg-brand-orange/15 px-3 py-1 text-xs font-medium text-ink hover:bg-brand-orange/30"
            >
              Follow-up needed
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          {selected.size > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmOpen(true)}
              className="border-brand-red/50 text-brand-red-dark hover:bg-brand-red/10 hover:text-brand-red-dark"
            >
              <Trash2 className="mr-1.5 h-4 w-4" />
              Delete {selected.size}
            </Button>
          )}
          <span className="text-sm text-ink-soft">
            {selected.size > 0
              ? `${selected.size} selected · ${calls.length} calls`
              : `${calls.length} calls`}
          </span>
        </div>
      </div>

      {calls.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <PhoneOutgoing className="h-12 w-12 text-ink-muted mb-4" />
          {followUpOnly ? (
            <>
              <p className="text-ink font-medium">
                No calls need follow-up
                {filter !== "all" && ` with status "${filter}"`}.
              </p>
              <button
                onClick={() => onFollowUpOnlyChange?.(false)}
                className="mt-2 text-sm text-brand hover:underline"
              >
                Clear follow-up filter
              </button>
            </>
          ) : filter === "all" ? (
            <>
              <p className="text-ink font-medium">No calls yet</p>
              <p className="text-sm text-ink-soft mt-1">
                Place your first demo call to see the pipeline in action.
              </p>
            </>
          ) : (
            <>
              <p className="text-ink font-medium">
                No {filter} calls in the last 100.
              </p>
              <button
                onClick={() => onFilterChange("all")}
                className="mt-2 text-sm text-brand hover:underline"
              >
                Clear filter
              </button>
            </>
          )}
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  checked={
                    allSelected ? true : someSelected ? "indeterminate" : false
                  }
                  onCheckedChange={(v) => toggleAll(v === true)}
                  aria-label="Select all"
                />
              </TableHead>
              <TableHead>Patient</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Follow-up</TableHead>
              <TableHead>DOB</TableHead>
              <TableHead>When</TableHead>
              <TableHead className="text-right">Duration</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {calls.map((call) => (
              <TableRow
                key={call.doc_id}
                className="cursor-pointer hover:bg-mist data-[state=selected]:bg-brand/10"
                data-state={selected.has(call.doc_id) ? "selected" : undefined}
                onClick={() => onSelect(call.doc_id)}
              >
                <TableCell
                  className="w-10"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Checkbox
                    checked={selected.has(call.doc_id)}
                    onCheckedChange={(v) => toggleOne(call.doc_id, v === true)}
                    aria-label={`Select call ${call.doc_id}`}
                  />
                </TableCell>
                <TableCell className="font-medium">
                  {call.patient_name ?? "—"}
                </TableCell>
                <TableCell className="text-ink-soft font-mono text-sm">
                  {formatPhone(call.to)}
                </TableCell>
                <TableCell>
                  <StatusChip callStatus={call.call_status} />
                </TableCell>
                <TableCell>
                  <OutcomeChip outcome={call.outcome} channel={call.channel} />
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-1.5">
                  {call.follow_up ? (
                    <span
                      title={reasonsFor(call).map((r) => `${reasonTierLabel(r.tier)}: ${r.text}`).join("\n") || call.follow_up_reason}
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${getFollowUpChipClasses(
                        call.follow_up
                      )}`}
                    >
                      {getFollowUpLabel(call.follow_up)}
                      {reasonsFor(call).length > 1 ? <span className="ml-1 opacity-70">· {reasonsFor(call).length} reasons</span> : null}
                    </span>
                  ) : (
                    <span className="text-sm text-ink-muted">—</span>
                  )}
                  <QuestionCountPill call={call} />
                  </div>
                </TableCell>
                <TableCell className="text-sm">
                  {call.dob_verification_result
                    ? DOB_LABEL[call.dob_verification_result] ?? call.dob_verification_result
                    : "—"}
                </TableCell>
                <TableCell className="text-sm text-ink-soft">
                  {relativeTime(call.initiated_at)}
                </TableCell>
                <TableCell className="text-right text-sm text-ink-soft">
                  {formatDuration(call.duration_seconds)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <DeleteCallsDialog
        open={confirmOpen}
        selectedSids={Array.from(selected)}
        onOpenChange={setConfirmOpen}
        onDeleted={afterDeleted}
      />
    </div>
  );
}
