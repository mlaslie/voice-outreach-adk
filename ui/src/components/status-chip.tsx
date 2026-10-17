import { getStatusClasses, getStatusLabel } from "@/lib/status";
import { Badge } from "@/components/ui/badge";

interface StatusChipProps {
  callStatus?: string;
}

export function StatusChip({ callStatus }: StatusChipProps) {
  const classes = getStatusClasses(callStatus);
  const label = getStatusLabel(callStatus);
  const isInFlight = !callStatus;

  return (
    <Badge className={`${classes} font-normal border-0 gap-1`}>
      {isInFlight && (
        <span className="h-1.5 w-1.5 rounded-full bg-brand-bright animate-pulse-dot" />
      )}
      {label}
    </Badge>
  );
}
