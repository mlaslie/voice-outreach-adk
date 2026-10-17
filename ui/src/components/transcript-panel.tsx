"use client";

import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TranscriptTurn } from "@/lib/types";
import { formatTime } from "@/lib/format";

interface TranscriptPanelProps {
  turns: TranscriptTurn[];
  loading: boolean;
  error: string | null;
  available: boolean | null;
  reason?: string;
  onReload: () => void;
  /** Fullscreen view: fill the container and show speaker/time captions. */
  expanded?: boolean;
}

export function TranscriptPanel({
  turns,
  loading,
  error,
  available,
  reason,
  onReload,
  expanded = false,
}: TranscriptPanelProps) {
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-3/4 ml-auto rounded-2xl" />
        <Skeleton className="h-8 w-2/3 rounded-2xl" />
        <Skeleton className="h-8 w-1/2 ml-auto rounded-2xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-4">
        <p className="text-sm text-brand-red-dark mb-2">{error}</p>
        <Button variant="outline" size="sm" onClick={onReload}>
          <RefreshCw className="h-3 w-3 mr-1" /> Retry
        </Button>
      </div>
    );
  }

  if (available === false) {
    return (
      <div className="text-center py-4">
        <p className="text-sm text-ink-soft mb-2">
          {reason ?? "No conversation to show."}
        </p>
      </div>
    );
  }

  if (turns.length === 0) {
    return (
      <div className="text-center py-4">
        <p className="text-sm text-ink-soft mb-2">
          Transcript not yet exported to BigQuery.
        </p>
        <Button variant="outline" size="sm" onClick={onReload}>
          <RefreshCw className="h-3 w-3 mr-1" /> Reload
        </Button>
      </div>
    );
  }

  return (
    <div
      className={`space-y-2 overflow-y-auto pr-1 ${
        expanded ? "h-full" : "max-h-72"
      }`}
    >
      {turns.map((turn, i) => {
        const isAgent = turn.role === "agent";
        return (
          <div
            key={i}
            className={`flex flex-col ${isAgent ? "items-start" : "items-end"}`}
          >
            {expanded && (
              <span className="text-[11px] text-ink-muted px-1 mb-0.5">
                {isAgent ? "Agent" : "Patient"}
                {turn.timestamp && ` · ${formatTime(turn.timestamp)}`}
              </span>
            )}
            <div
              className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                isAgent
                  ? "bg-brand/10 text-brand-deep"
                  : "bg-mist text-ink"
              }`}
            >
              {turn.text}
            </div>
          </div>
        );
      })}
    </div>
  );
}
