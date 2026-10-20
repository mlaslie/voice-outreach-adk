import { describe, expect, it } from "vitest";
import { describeAction, describeTarget } from "./audit";

describe("describeAction", () => {
  it.each([
    ["PUT /api/call-settings/display", "Changed time display"],
    ["DELETE /api/allowlist/{phone}", "Removed an authorized number"],
    ["POST /api/allowlist", "Authorized a number"],
    ["PUT /api/call-settings/recall", "Changed calling hours and auto-recall"],
    ["POST /api/sms/control", "Switched the shared SMS number"],
    ["DELETE /api/sms/callbacks/{callback_id}", "Cancelled a booked callback"],
    ["POST /api/calls/batch-delete", "Deleted calls"],
  ])("%s reads as %s", (action, words) => {
    expect(describeAction(action)).toBe(words);
  });

  it("tolerates extra whitespace", () => {
    expect(describeAction("  PUT   /api/call-settings/display ")).toBe("Changed time display");
  });

  it("falls back to the raw action", () => {
    expect(describeAction("POST /api/something-new")).toBe("POST /api/something-new");
    expect(describeAction("")).toBe("");
  });
});

describe("describeTarget", () => {
  it("names each path parameter", () => {
    expect(describeTarget({ phone: "+1•••••0123" })).toBe("Number +1•••••0123");
    expect(describeTarget({ run_id: "abc", entry_id: "e1" })).toBe("Run abc · Patient record e1");
  });

  it("keeps an unknown key as it is", () => {
    expect(describeTarget({ widget: "w1" })).toBe("widget w1");
  });

  it("is empty when there is no target", () => {
    expect(describeTarget({})).toBe("");
    expect(describeTarget(null)).toBe("");
    expect(describeTarget({ phone: "" })).toBe("");
  });
});
