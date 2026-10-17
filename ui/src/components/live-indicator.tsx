interface LiveIndicatorProps {
  state: "live" | "polling" | "disconnected";
}

export function LiveIndicator({ state }: LiveIndicatorProps) {
  if (state === "live") {
    return (
      <span className="flex items-center gap-1.5 text-sm text-brand">
        <span className="h-2 w-2 rounded-full bg-brand-bright animate-pulse-dot" />
        Live
      </span>
    );
  }
  if (state === "polling") {
    return (
      <span className="flex items-center gap-1.5 text-sm text-ink-soft">
        <span className="h-2 w-2 rounded-full bg-ink-muted" />
        Polling
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-sm text-brand-red">
      <span className="h-2 w-2 rounded-full bg-brand-coral" />
      Disconnected
    </span>
  );
}
