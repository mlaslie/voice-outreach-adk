import { MessageSquare, Phone, PhoneOff } from "lucide-react";

import { CallDoc } from "@/lib/types";

/**
 * How this follow-up was actually completed.
 *
 * Worth its own banner because the call record alone is misleading: a
 * questionnaire finished over SMS lands on the call doc as `success` with a
 * full set of answers, which reads exactly like a call the patient picked up.
 * Whoever reviews this needs to know nobody spoke to them.
 *
 * The two are colour-coded rather than only labelled, so the distinction
 * survives a quick scan down a list of records.
 */
export function CompletionFlag({ call }: { call: CallDoc }) {
  const answered = !!call.outcome;
  const byText = call.channel === "sms";

  if (!answered) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-rule bg-mist/60 px-3 py-2 text-sm text-ink-soft">
        <PhoneOff className="h-4 w-4 shrink-0" />
        <span>
          No questionnaire was completed for this call.
          {call.call_status === "left_vmail" && " A voicemail was left."}
        </span>
      </div>
    );
  }

  if (byText) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-brand-blue/30 bg-brand-blue/10 px-3 py-2 text-sm text-brand-blue-dark">
        <MessageSquare className="h-4 w-4 shrink-0" />
        <span>
          <strong className="font-semibold">Completed by text.</strong> The
          patient answered the questionnaire over SMS — this call was not
          answered.
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border border-brand/30 bg-brand/10 px-3 py-2 text-sm text-brand-dark">
      <Phone className="h-4 w-4 shrink-0" />
      <span>
        <strong className="font-semibold">Completed by voice.</strong> The
        patient answered the questionnaire on this call.
      </span>
    </div>
  );
}
