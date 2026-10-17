import { CallDoc } from "@/lib/types";
import { getDobChipClasses } from "@/lib/status";
import { Badge } from "@/components/ui/badge";

interface SurveyAnswersProps {
  call: CallDoc;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-1.5 border-b border-rule last:border-0">
      <dt className="w-36 shrink-0 text-sm text-ink-soft">{label}</dt>
      <dd className="text-sm flex-1">{value}</dd>
    </div>
  );
}

export function SurveyAnswers({ call }: SurveyAnswersProps) {
  const hasOutcome = !!call.outcome;

  if (!hasOutcome) {
    return (
      <p className="text-sm text-ink-soft italic">No survey collected on this call.</p>
    );
  }

  const dobClasses = getDobChipClasses(call.dob_verification_result);

  return (
    <dl>
      {call.dob_verification_result && (
        <Row
          label="DOB verification"
          value={
            <Badge className={`${dobClasses} font-normal border-0`}>
              {call.dob_verification_result}
            </Badge>
          }
        />
      )}
      {call.outcome && <Row label="Outcome" value={call.outcome.replace(/_/g, " ")} />}
      {call.follow_up && (
        <Row
          label="Follow-up"
          value={
            <Badge className="bg-brand-orange/25 text-ink font-normal border-0">
              {call.follow_up.replace(/_/g, " ")}
            </Badge>
          }
        />
      )}
      {call.follow_up_reason && (
        <Row label="Reason" value={`"${call.follow_up_reason}"`} />
      )}
      {call.survey_responses && (
        <>
          {/* Underscore keys are bookkeeping the service carries alongside
              the answers -- the raw text of each reply, and which answers
              triggered the follow-up. They are not questions anyone asked. */}
          {Object.entries(call.survey_responses)
            .filter(([key]) => !key.startsWith("_"))
            .map(([key, val]) => (
              <Row
                key={key}
                label={key.replace(/_/g, " ")}
                value={String(val)}
              />
            ))}
        </>
      )}
    </dl>
  );
}
