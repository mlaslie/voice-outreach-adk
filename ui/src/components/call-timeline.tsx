import { CallDoc } from "@/lib/types";
import { formatTime } from "@/lib/format";

interface CallTimelineProps {
  call: CallDoc;
}

interface TimelineEvent {
  label: string;
  time?: string;
  note?: string;
}

export function CallTimeline({ call }: CallTimelineProps) {
  const events: TimelineEvent[] = [
    { label: "Initiated", time: call.initiated_at },
    { label: "Ringing", time: call.ringing_at },
    { label: "Answered", time: call.answered_at },
  ];

  const endedNote = call.failure_code
    ? `${call.failure_code}${call.failure_message ? ` — ${call.failure_message}` : ""}`
    : call.failure_reason
    ? call.failure_reason
    : call.call_status
    ? `(${call.call_status})`
    : undefined;

  events.push({ label: "Ended", time: call.ended_at, note: endedNote });

  return (
    <div className="space-y-2">
      {events.map((ev, i) => {
        if (!ev.time && i > 0) return null;
        return (
          <div key={ev.label} className="flex items-start gap-3">
            <div className="mt-1 h-2.5 w-2.5 rounded-full bg-ink-muted shrink-0" />
            <div>
              <span className="text-sm font-medium">{ev.label}</span>
              {ev.time && (
                <span className="ml-2 text-sm text-ink-soft">{formatTime(ev.time)}</span>
              )}
              {ev.note && <p className="text-xs text-ink-soft mt-0.5">{ev.note}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
