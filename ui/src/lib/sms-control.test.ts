import { describe, expect, it } from "vitest";
import type { SmsControlStatus } from "./api";
import { demoSmsControl, flipLocally, smsButton, smsConfirm, smsIndicator } from "./sms-control";

const AT = "2026-10-19T14:05:00Z";
const fmt = () => "14:05";

function status(over: Partial<SmsControlStatus> = {}): SmsControlStatus {
  return {
    enabled: true,
    self_name: "ADK",
    peer_name: "GE-CX",
    number_last4: "0100",
    can_give: true,
    dry_run: false,
    checked_at: AT,
    active: "self",
    active_name: "ADK",
    open_conversations: 0,
    ...over,
  };
}

describe("smsIndicator", () => {
  it("is filled and named ADK when this system is active", () => {
    expect(smsIndicator(status(), null, fmt)).toEqual({
      label: "ADK active",
      variant: "self",
      tooltip: "Incoming texts on the number ending 0100 go to ADK. Checked 14:05.",
    });
  });

  it("is outlined and named GE-CX when the other system is active", () => {
    const i = smsIndicator(status({ active: "peer", active_name: "GE-CX" }), null, fmt);
    expect(i?.label).toBe("GE-CX active");
    expect(i?.variant).toBe("peer");
    expect(i?.tooltip).toBe("Incoming texts on the number ending 0100 go to GE-CX. Checked 14:05.");
  });

  it("shows Unknown with the webhook host when neither URL matches", () => {
    const i = smsIndicator(status({ active: "unknown", active_name: null, webhook_host: "hooks.example.org" }), null, fmt);
    expect(i?.label).toBe("SMS: Unknown");
    expect(i?.variant).toBe("unknown");
    expect(i?.tooltip).toContain("hooks.example.org");
  });

  it("includes the backend's error when the number could not be read", () => {
    const i = smsIndicator(status({ active: "unknown", active_name: null, error: "Twilio lookup failed." }), null, fmt);
    expect(i?.tooltip).toMatch(/Twilio lookup failed\.$/);
  });

  it("is hidden when the switch is off", () => {
    expect(smsIndicator(status({ enabled: false }), null, fmt)).toBeNull();
  });

  it("is hidden until the first read, and Unknown if that read failed", () => {
    expect(smsIndicator(null)).toBeNull();
    expect(smsIndicator(null, "Bad gateway")).toMatchObject({ label: "SMS: Unknown", variant: "unknown" });
  });
});

describe("smsButton", () => {
  it("offers to give control away when this system is active", () => {
    expect(smsButton(status())).toMatchObject({ action: "give", label: "Give control to GE-CX", target: "GE-CX", disabled: false });
  });

  it("is disabled with a reason when the other system's URL is not configured", () => {
    const b = smsButton(status({ can_give: false }));
    expect(b.disabled).toBe(true);
    expect(b.reason).toBeTruthy();
  });

  it.each(["peer", "unknown"] as const)("offers Take control when %s is active", (active) => {
    expect(smsButton(status({ active, can_give: false }))).toMatchObject({
      action: "take",
      label: "Take control",
      target: "ADK",
      disabled: false,
    });
  });
});

describe("smsConfirm", () => {
  it("names the last 4 digits and the target", () => {
    expect(smsConfirm(status({ active: "peer" }), "take")).toEqual({
      question: "Send incoming texts on the number ending 0100 to ADK?",
      warning: null,
    });
  });

  it("warns about this system's open conversations when giving control away", () => {
    expect(smsConfirm(status({ open_conversations: 1 }), "give").warning).toBe(
      "1 open text conversation here — replies to them will go to GE-CX.",
    );
    expect(smsConfirm(status({ open_conversations: 3 }), "give").warning).toBe(
      "3 open text conversations here — replies to them will go to GE-CX.",
    );
  });

  it("does not warn with no open conversations, or when taking control", () => {
    expect(smsConfirm(status(), "give").warning).toBeNull();
    expect(smsConfirm(status({ active: "peer", open_conversations: 2 }), "take").warning).toBeNull();
  });
});

describe("sample data", () => {
  it("flips locally", () => {
    const s = demoSmsControl(AT);
    const given = flipLocally(s, "give", AT);
    expect(given.active).toBe("peer");
    expect(smsIndicator(given, null, fmt)?.label).toBe("GE-CX active");
    expect(flipLocally(given, "take", AT).active).toBe("self");
  });
});
