"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { fmtHHMM, useTimeFormat } from "@/lib/time";
import { joinLocalDateTime, splitLocalDateTime, timeOptions } from "@/lib/time-input";

/**
 * Time pickers that follow the clinic's 12h/24h setting.
 *
 * Native time and datetime-local inputs use the OS locale's clock and cannot
 * be forced either way, so these use a <select> of times (labels in the
 * clinic format, values always "HH:MM" 24-hour) and, for a date and time, a
 * native date input beside it. The value at the boundary is unchanged:
 * "HH:MM" for a time, "YYYY-MM-DDTHH:MM" (datetime-local) for both.
 */

/** Matches the shadcn Input look, for forms built from it. */
export const INPUT_LOOK =
  "h-9 rounded-md border border-input bg-transparent px-2.5 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function ClinicTimeSelect({
  value,
  onChange,
  onBlur,
  id,
  "aria-label": ariaLabel,
  step = 15,
  disabled,
  allowEmpty = false,
  className,
}: {
  /** "HH:MM", 24-hour; "" when blank (only with allowEmpty). */
  value: string;
  onChange: (hhmm: string) => void;
  onBlur?: () => void;
  id?: string;
  "aria-label"?: string;
  /** Minutes between options. A current value off the grid is still listed. */
  step?: number;
  disabled?: boolean;
  allowEmpty?: boolean;
  className?: string;
}) {
  const format = useTimeFormat();
  const options = timeOptions(step, value);
  return (
    <select
      id={id}
      aria-label={ariaLabel}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
      className={cn("tabular-nums", className)}
    >
      {allowEmpty || !value ? <option value="">{format === "24h" ? "--:--" : "--:-- --"}</option> : null}
      {options.map((t) => <option key={t} value={t}>{fmtHHMM(t, format)}</option>)}
    </select>
  );
}

export function ClinicDateTimeInput({
  value,
  onChange,
  onBlur,
  id,
  label = "Date and time",
  step = 15,
  disabled,
  defaultTime = "09:00",
  className,
  inputClassName,
}: {
  /** "YYYY-MM-DDTHH:MM" (the datetime-local format), or "". */
  value: string;
  onChange: (local: string) => void;
  onBlur?: () => void;
  /** Goes on the date input (so a <label htmlFor> lands on it); the time select gets `${id}-time`. */
  id?: string;
  /** Accessible name prefix: "<label> date" / "<label> time". */
  label?: string;
  step?: number;
  disabled?: boolean;
  /** Time used when a date is picked before a time. */
  defaultTime?: string;
  className?: string;
  /** Styling for both controls. */
  inputClassName?: string;
}) {
  const { date, time } = splitLocalDateTime(value);
  // A time picked while the date is blank has no value to live in yet.
  const [pendingTime, setPendingTime] = useState("");
  const shownTime = time || pendingTime;

  const setDate = (d: string) => onChange(d ? joinLocalDateTime(d, shownTime || defaultTime) : "");
  const setTime = (t: string) => {
    if (date) onChange(t ? joinLocalDateTime(date, t) : "");
    else setPendingTime(t);
  };

  return (
    <span className={cn("flex flex-wrap items-center gap-2", className)}>
      <input
        id={id}
        type="date"
        aria-label={`${label} date`}
        value={date}
        disabled={disabled}
        onChange={(e) => setDate(e.target.value)}
        onBlur={onBlur}
        className={cn("min-w-0", inputClassName)}
      />
      <ClinicTimeSelect
        id={id ? `${id}-time` : undefined}
        aria-label={`${label} time`}
        value={shownTime}
        onChange={setTime}
        onBlur={onBlur}
        step={step}
        disabled={disabled}
        allowEmpty={!date}
        className={inputClassName}
      />
    </span>
  );
}
