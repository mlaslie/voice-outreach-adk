import { MessageSquare, Phone } from "lucide-react";

import { Badge } from "@/components/ui/badge";

interface OutcomeChipProps {
  outcome?: string;
  /**
   * How the answers arrived. Without this a questionnaire completed over SMS
   * is indistinguishable from one done on the phone, so the list shows a
   * "successful call" for a call nobody actually answered.
   */
  channel?: "voice" | "sms";
}

export function OutcomeChip({ outcome, channel }: OutcomeChipProps) {
  if (!outcome) return <span className="text-ink-muted">—</span>;

  const isIdFail = outcome === "identity_verification_failed";
  const classes = isIdFail
    ? "bg-brand-pink/15 text-brand-pink-dark border-0 font-normal"
    : "bg-mist text-ink border-0 font-normal";

  const label = outcome.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  // How the answers arrived, colour-coded the same way as the flag on the
  // call record: green for voice, blue for text. Both are labelled rather
  // than only one, so a row without a channel chip means "no questionnaire"
  // instead of ambiguously meaning "voice, probably".
  const byText = channel === "sms";
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge className={classes}>{label}</Badge>
      <Badge
        title={
          byText
            ? "The patient answered by text, not on the call"
            : "The patient answered on the call"
        }
        className={
          byText
            ? "bg-brand-blue/10 text-brand-blue-dark border-0 font-normal gap-1"
            : "bg-brand/10 text-brand-dark border-0 font-normal gap-1"
        }
      >
        {byText ? (
          <MessageSquare className="h-3 w-3" />
        ) : (
          <Phone className="h-3 w-3" />
        )}
        {byText ? "By text" : "By voice"}
      </Badge>
    </span>
  );
}
