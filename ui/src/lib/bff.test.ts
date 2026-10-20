import { describe, expect, it } from "vitest";
import { ALLOWED_ROUTES, actingUser, checkWrite, matchRoute, needsIdToken } from "./bff";

const SID = "CA0123456789abcdef0123456789abcdef";
const ID32 = "0123456789abcdef0123456789abcdef";

describe("matchRoute: the BFF path allowlist", () => {
  it.each([
    ["GET", "calls"],
    ["GET", "calls/stream"],
    ["GET", `calls/${SID}`],
    ["GET", `calls/${SID}/transcript`],
    ["GET", `calls/${SID}/recording`],
    ["POST", `calls/${SID}/follow-up`],
    ["POST", "calls/batch-delete"],
    ["GET", "follow-ups/open"],
    ["POST", "place-call"],
    ["POST", "test-call"],
    ["GET", "call-list"],
    ["POST", "call-list"],
    ["POST", "call-list/bulk"],
    ["POST", "call-list/reset"],
    ["POST", "call-list/cancel"],
    ["GET", "call-list/run"],
    ["POST", "call-list/run"],
    ["POST", "call-list/run/cancel"],
    ["PATCH", `call-list/${ID32}`],
    ["DELETE", `call-list/${ID32}`],
    ["POST", `call-list/${ID32}/reschedule`],
    ["GET", "call-runs"],
    ["POST", "call-runs"],
    ["PATCH", `call-runs/${ID32}`],
    ["DELETE", `call-runs/${ID32}`],
    ["GET", "call-settings"],
    ["PUT", "call-settings/recall"],
    ["PUT", "call-settings/display"],
    ["GET", "allowlist"],
    ["POST", "allowlist"],
    ["DELETE", "allowlist/%2B12025550123"],
    ["GET", "sms/sessions"],
    ["GET", "sms/callbacks"],
    ["DELETE", "sms/callbacks/cb_123"],
    ["GET", "sms/messages"],
    ["GET", "sms/consent"],
    ["POST", "sms/consent"],
    ["GET", "sms/settings"],
    ["POST", "sms/settings"],
    ["POST", "sms/test/start"],
    ["POST", "sms/test/reply"],
    ["GET", "sms/control"],
    ["POST", "sms/control"],
  ])("allows %s %s (in the build that owns it)", (method, path) => {
    expect(matchRoute(method, path, "nurse") ?? matchRoute(method, path, "admin")).not.toBeNull();
  });

  it("is case-insensitive on the method only", () => {
    expect(matchRoute("get", "calls")).not.toBeNull();
    expect(matchRoute("GET", "Calls")).toBeNull();
  });

  it.each([
    ["DELETE", "calls"], // right path, wrong method
    ["GET", "place-call"],
    ["POST", "calls/stream"],
    ["PUT", "call-settings"],
    ["GET", "call-settings/display"],
    ["PUT", "call-settings/display/x"],
    ["POST", "follow-ups/open"],
    ["POST", "tasks/tick"], // the endpoint that dials people on a timer
    ["POST", "sms/tasks/tick"],
    ["GET", "sms/inbound"],
    ["GET", "sms/test/start"], // the simulator is write-only
    ["POST", "sms/test"],
    ["POST", "sms/test/send"],
    ["POST", "twilio/voice"],
    ["PUT", "sms/control"], // the switch: GET and POST only
    ["PATCH", "sms/control"],
    ["DELETE", "sms/control"],
    ["GET", "sms/control/take"],
    ["POST", "sms/control/give"],
    ["POST", "sms/controls"],
    ["GET", "SMS/control"],
    ["POST", "twilio/sms"], // the webhook itself is never reachable through the BFF
    ["GET", "admin"],
    ["GET", ""],
    ["GET", "calls/"],
    ["GET", "/calls"],
    ["GET", "calls//transcript"],
  ])("refuses %s %s", (method, path) => {
    expect(matchRoute(method, path, "nurse")).toBeNull();
    expect(matchRoute(method, path, "admin")).toBeNull();
  });

  it.each([
    "calls/..%2Ftasks%2Ftick", // encoded separator
    "calls/%2E%2E/tasks/tick", // encoded dot-dot
    "calls/../tasks/tick",
    "calls/./stream",
    `calls/${SID}%2Ftranscript`,
    "calls/a%5Cb", // backslash
    "calls/%E0%A4%A", // malformed escape
    "calls/x%3Fy", // encoded "?"
    "calls/x%23y", // encoded "#"
  ])("refuses traversal / smuggling in %s", (path) => {
    expect(matchRoute("GET", path)).toBeNull();
  });

  it("refuses an allowlist phone that is not E.164", () => {
    expect(matchRoute("DELETE", "allowlist/not-a-phone", "admin")).toBeNull();
  });

  it("refuses over-long ids", () => {
    expect(matchRoute("GET", `calls/${"a".repeat(65)}`)).toBeNull();
  });

  it("re-encodes segments for the upstream URL", () => {
    expect(matchRoute("DELETE", "allowlist/%2B12025550123", "admin")?.upstreamPath).toBe(
      "allowlist/%2B12025550123"
    );
    expect(matchRoute("GET", `calls/${SID}/transcript`)?.upstreamPath).toBe(`calls/${SID}/transcript`);
  });
});

describe("matchRoute: each build calls only its own routes", () => {
  // Configuration: the admin console only. The API refuses these from the
  // nurse UI's identity, so the nurse build must never send them.
  const ADMIN_ROUTES: [string, string][] = [
    ["POST", "call-list"],
    ["POST", "call-list/bulk"],
    ["PATCH", `call-list/${ID32}`],
    ["DELETE", `call-list/${ID32}`],
    ["POST", "call-list/reset"],
    ["POST", "call-runs"],
    ["PATCH", `call-runs/${ID32}`],
    ["DELETE", `call-runs/${ID32}`],
    ["PUT", "call-settings/recall"],
    ["PUT", "call-settings/display"],
    ["PUT", "call-settings/organization"],
    ["POST", "allowlist"],
    ["DELETE", "allowlist/%2B12025550123"],
    ["POST", "sms/consent"],
    ["POST", "sms/settings"],
    ["POST", "sms/control"],
    ["POST", "sms/test/start"],
    ["POST", "sms/test/reply"],
  ];
  // Clinical work: the nurse console only.
  const NURSE_ROUTES: [string, string][] = [
    ["POST", `calls/${SID}/follow-up`],
    ["POST", "calls/batch-delete"],
    ["POST", "place-call"],
    ["POST", "test-call"],
    ["POST", "call-list/cancel"],
    ["POST", `call-list/${ID32}/reschedule`],
    ["POST", "call-list/run"],
    ["POST", "call-list/run/cancel"],
    ["DELETE", "sms/callbacks/cb_123"],
  ];
  // Not proxied at all yet (no screen uses them); refused by both builds,
  // and in particular never reachable from the admin build.
  const NURSE_NOT_PROXIED: [string, string][] = [
    ["POST", "calls/sign-off"],
    ["POST", `calls/${SID}/sign-off`],
    ["POST", `calls/${SID}/recording-link`],
  ];
  const READS: [string, string][] = [
    ["GET", "calls"],
    ["GET", "calls/stream"],
    ["GET", `calls/${SID}`],
    ["GET", `calls/${SID}/transcript`],
    ["GET", `calls/${SID}/recording`],
    ["GET", "follow-ups/open"],
    ["GET", "call-list"],
    ["GET", "call-list/run"],
    ["GET", "call-runs"],
    ["GET", "call-settings"],
    ["GET", "allowlist"],
    ["GET", "sms/sessions"],
    ["GET", "sms/callbacks"],
    ["GET", "sms/messages"],
    ["GET", "sms/consent"],
    ["GET", "sms/settings"],
    ["GET", "sms/control"],
  ];

  it.each(ADMIN_ROUTES)("the nurse build refuses admin route %s %s; the admin build allows it", (method, path) => {
    expect(matchRoute(method, path, "nurse")).toBeNull();
    expect(matchRoute(method, path, "admin")).not.toBeNull();
  });

  it.each(NURSE_ROUTES)("the admin build refuses nurse route %s %s; the nurse build allows it", (method, path) => {
    expect(matchRoute(method, path, "admin")).toBeNull();
    expect(matchRoute(method, path, "nurse")).not.toBeNull();
  });

  it.each(NURSE_NOT_PROXIED)("the admin build refuses %s %s", (method, path) => {
    expect(matchRoute(method, path, "admin")).toBeNull();
  });

  it.each(READS)("both builds allow %s %s", (method, path) => {
    expect(matchRoute(method, path, "nurse")).not.toBeNull();
    expect(matchRoute(method, path, "admin")).not.toBeNull();
  });

  it("tags every GET 'both' and every write 'nurse' or 'admin'", () => {
    for (const [method, re, scope] of ALLOWED_ROUTES) {
      if (method === "GET") expect(scope, String(re)).toBe("both");
      else expect(["nurse", "admin"], `${method} ${re}`).toContain(scope);
    }
  });

  it("lists every write route above, so a new one has to be placed in a build", () => {
    const writes = ALLOWED_ROUTES.filter(([m]) => m !== "GET").length;
    expect(ADMIN_ROUTES.length + NURSE_ROUTES.length).toBe(writes);
    expect(READS.length).toBe(ALLOWED_ROUTES.length - writes);
  });

  it("defaults to the nurse build when no mode is given (NEXT_PUBLIC_APP_MODE unset)", () => {
    expect(matchRoute("POST", "call-list/run")).not.toBeNull();
    expect(matchRoute("POST", "call-runs")).toBeNull();
  });
});

describe("checkWrite: cross-site write refusal (U-2)", () => {
  const host = "dashboard.example.org";
  const json = "application/json";

  it("lets reads through without any checks", () => {
    expect(checkWrite("GET", {}, false)).toEqual({ ok: true });
  });

  it("accepts a same-origin fetch with a JSON body", () => {
    expect(checkWrite("POST", { "sec-fetch-site": "same-origin", "content-type": json }, true)).toEqual({ ok: true });
  });

  it("accepts a JSON content type with parameters", () => {
    const r = checkWrite("PATCH", { "sec-fetch-site": "same-origin", "content-type": "application/json; charset=utf-8" }, true);
    expect(r.ok).toBe(true);
  });

  it("accepts a body-less same-origin write without a content type", () => {
    expect(checkWrite("DELETE", { "sec-fetch-site": "same-origin" }, false).ok).toBe(true);
    expect(checkWrite("POST", { "sec-fetch-site": "same-origin" }, false).ok).toBe(true);
  });

  it.each(["cross-site", "same-site", "none"])("refuses Sec-Fetch-Site: %s", (site) => {
    const r = checkWrite("POST", { "sec-fetch-site": site, "content-type": json, origin: `https://${host}`, host }, true);
    expect(r).toMatchObject({ ok: false, status: 403 });
  });

  it("falls back to Origin when Sec-Fetch-Site is absent", () => {
    expect(checkWrite("POST", { origin: `https://${host}`, host, "content-type": json }, true).ok).toBe(true);
    expect(checkWrite("POST", { origin: "https://evil.example.net", host, "content-type": json }, true)).toMatchObject({
      ok: false,
      status: 403,
    });
  });

  it("prefers X-Forwarded-Host (behind a load balancer) over Host", () => {
    const h = { origin: `https://${host}`, host: "0.0.0.0:8080", "x-forwarded-host": host, "content-type": json };
    expect(checkWrite("PUT", h, true).ok).toBe(true);
  });

  it("refuses a write with neither Sec-Fetch-Site nor Origin", () => {
    expect(checkWrite("DELETE", { host }, false)).toMatchObject({ ok: false, status: 403 });
  });

  it("refuses Origin: null (sandboxed iframe, data: URL)", () => {
    expect(checkWrite("POST", { origin: "null", host, "content-type": json }, true)).toMatchObject({ ok: false, status: 403 });
  });

  it("refuses a malformed Origin", () => {
    expect(checkWrite("POST", { origin: "not a url", host, "content-type": json }, true)).toMatchObject({ ok: false, status: 403 });
  });

  it.each(["application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain", undefined])(
    "refuses a body sent as %s (what an HTML form can send)",
    (ct) => {
      const r = checkWrite("POST", { "sec-fetch-site": "same-origin", "content-type": ct }, true);
      expect(r).toMatchObject({ ok: false, status: 415 });
    }
  );
});

describe("actingUser", () => {
  it("strips the IAP namespace", () => {
    expect(actingUser("accounts.google.com:nurse@example.org")).toBe("nurse@example.org");
  });
  it("returns null when absent", () => {
    expect(actingUser(null)).toBeNull();
    expect(actingUser("")).toBeNull();
    expect(actingUser("accounts.google.com:")).toBeNull();
  });
});

describe("needsIdToken", () => {
  it("skips the token for a local backend", () => {
    expect(needsIdToken("http://localhost:8081")).toBe(false);
    expect(needsIdToken("http://localhost")).toBe(false);
    expect(needsIdToken("http://127.0.0.1:8081")).toBe(false);
  });
  it("requires it for anything else", () => {
    expect(needsIdToken("https://api.example.org")).toBe(true);
    expect(needsIdToken("http://localhost.example.org")).toBe(true);
    expect(needsIdToken("https://localhost:8081")).toBe(true);
  });
});
