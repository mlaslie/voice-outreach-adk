/**
 * The shared SMS number switch, as pure functions (docs/sms-number-switch.md).
 *
 * The wording here is part of the contract: the other system's dashboard
 * shows exactly the same labels, buttons and confirm text, so a tester sees
 * one vocabulary whichever dashboard is open.
 */
import type { SmsControlAction, SmsControlStatus } from "./api";
import { fmtClock } from "./time";

export const SELF_NAME = "ADK";
export const PEER_NAME = "GE-CX";

/** How often the indicator re-reads the switch (the contract's maximum cache age). */
export const SMS_CONTROL_POLL_MS = 30_000;

/** Filled when this system is active, outlined when the other one is, amber when neither matches. */
export type SmsVariant = "self" | "peer" | "unknown";

export interface SmsIndicator {
  label: string;
  variant: SmsVariant;
  tooltip: string;
}

type Fmt = (iso: string) => string;
const defaultFmt: Fmt = (iso) => fmtClock(iso);

function selfName(s: SmsControlStatus): string {
  return s.self_name || SELF_NAME;
}

function peerName(s: SmsControlStatus): string {
  return s.peer_name || PEER_NAME;
}

/** "1234", or a placeholder when the number could not be read. */
export function last4(s: SmsControlStatus): string {
  return s.number_last4 || "····";
}

/** Who receives incoming texts right now, in words. */
export function activeName(s: SmsControlStatus): string {
  if (s.active === "self") return selfName(s);
  if (s.active === "peer") return peerName(s);
  return s.webhook_host ? `an unrecognised address (${s.webhook_host})` : "an unknown address";
}

export function checkedText(s: SmsControlStatus, fmt: Fmt = defaultFmt): string {
  return s.checked_at ? `Checked ${fmt(s.checked_at)}.` : "Not checked yet.";
}

/**
 * The top-right pill. Null when it should not show: not loaded yet, or the
 * switch is off on this deployment. A failed read with nothing loaded shows
 * as Unknown (fail closed: this system is then not active).
 */
export function smsIndicator(
  status: SmsControlStatus | null,
  loadError: string | null = null,
  fmt: Fmt = defaultFmt,
): SmsIndicator | null {
  if (!status) {
    if (!loadError) return null;
    return { label: "SMS: Unknown", variant: "unknown", tooltip: `Could not read the SMS number: ${loadError}` };
  }
  if (!status.enabled) return null;
  const variant: SmsVariant = status.active === "self" || status.active === "peer" ? status.active : "unknown";
  const label =
    variant === "self" ? `${selfName(status)} active` : variant === "peer" ? `${peerName(status)} active` : "SMS: Unknown";
  let tooltip = `Incoming texts on the number ending ${last4(status)} go to ${activeName(status)}. ${checkedText(status, fmt)}`;
  if (status.error) tooltip += ` ${status.error}`;
  return { label, variant, tooltip };
}

export interface SmsButton {
  action: SmsControlAction;
  label: string;
  /** The system that receives texts after the flip. */
  target: string;
  disabled: boolean;
  /** Why the button is disabled (its tooltip). */
  reason: string | null;
}

/** The card's one button: "Take control" unless this system is active. */
export function smsButton(s: SmsControlStatus): SmsButton {
  if (s.active === "self") {
    const disabled = !s.can_give;
    return {
      action: "give",
      label: `Give control to ${peerName(s)}`,
      target: peerName(s),
      disabled,
      reason: disabled
        ? `${peerName(s)}'s incoming-text address is not configured on this system, so control cannot be given from here. Take control from the ${peerName(s)} dashboard instead.`
        : null,
    };
  }
  return { action: "take", label: "Take control", target: selfName(s), disabled: false, reason: null };
}

export interface SmsConfirm {
  question: string;
  /** Shown when this system is losing control with conversations still open. */
  warning: string | null;
}

export function smsConfirm(s: SmsControlStatus, action: SmsControlAction): SmsConfirm {
  const target = action === "give" ? peerName(s) : selfName(s);
  const question = `Send incoming texts on the number ending ${last4(s)} to ${target}?`;
  const losingIsSelf = action === "give" && s.active === "self";
  const n = s.open_conversations ?? 0;
  const warning =
    losingIsSelf && n > 0
      ? `${n} open text ${n === 1 ? "conversation" : "conversations"} here — replies to them will go to ${target}.`
      : null;
  return { question, warning };
}

export const DRY_RUN_NOTE = "This system is in dry-run: replies it receives are not answered by text yet.";

/** What a flip would return, for fictional sample data (nothing is changed). */
export function flipLocally(s: SmsControlStatus, action: SmsControlAction, nowIso: string): SmsControlStatus {
  const active = action === "take" ? "self" : "peer";
  return {
    ...s,
    active,
    active_name: active === "self" ? selfName(s) : peerName(s),
    webhook_host: null,
    error: null,
    checked_at: nowIso,
  };
}

/** Fictional state for the sample console: number ending 0100 (a reserved 555-01xx number). */
export function demoSmsControl(nowIso: string): SmsControlStatus {
  return {
    enabled: true,
    self_name: SELF_NAME,
    peer_name: PEER_NAME,
    number_last4: "0100",
    can_give: true,
    dry_run: true,
    checked_at: nowIso,
    active: "self",
    active_name: SELF_NAME,
    webhook_host: null,
    error: null,
    open_conversations: 1,
  };
}
