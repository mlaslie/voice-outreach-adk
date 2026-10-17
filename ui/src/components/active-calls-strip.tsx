"use client";

import { useEffect, useState } from "react";
import { CallDoc } from "@/lib/types";
import { formatPhone, elapsedSince } from "@/lib/format";

interface ActiveCallsStripProps {
  calls: CallDoc[];
  onSelect: (sid: string) => void;
}

function callPhase(call: CallDoc): string {
  if (call.answered_at) return "In progress";
  if (call.ringing_at) return "Ringing…";
  return "Queued";
}

function PhaseTimer({ call }: { call: CallDoc }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const start = call.answered_at ?? call.ringing_at ?? call.initiated_at;
  const elapsed = elapsedSince(start);

  return <span className="text-xs text-ink-soft">{elapsed}</span>;
}

export function ActiveCallsStrip({ calls, onSelect }: ActiveCallsStripProps) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-ink-soft mb-2">
        Currently Active ({calls.length})
      </p>
      <div className="flex gap-3 flex-wrap">
        {calls.map((call) => (
          <button
            key={call.doc_id}
            onClick={() => onSelect(call.doc_id)}
            className="flex-shrink-0 rounded-lg border border-brand-turquoise/60 bg-brand-turquoise/10 p-3 text-left hover:bg-brand-turquoise/25 transition-colors min-w-[200px]"
          >
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-brand-bright animate-pulse-dot" />
              <span className="text-sm font-medium">
                {call.patient_name ?? formatPhone(call.to)}
              </span>
            </div>
            <div className="mt-1 text-xs text-ink-soft">
              {formatPhone(call.to)}
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span className="text-xs text-ink-soft">{callPhase(call)}</span>
              <PhaseTimer call={call} />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
