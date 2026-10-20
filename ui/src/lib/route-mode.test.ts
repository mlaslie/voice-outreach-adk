import { describe, expect, it } from "vitest";
import { decideRoute } from "./route-mode";
import { consoleUrl, parseAppMode } from "./mode";

describe("decideRoute: pages per build", () => {
  it.each(["/api/backend/calls", "/api/backend/call-runs", "/health", "/_next/static/chunks/a.js", "/favicon.ico", "/brand/logo.svg"])(
    "serves %s in both builds",
    (path) => {
      expect(decideRoute("nurse", path)).toEqual({ action: "next" });
      expect(decideRoute("admin", path)).toEqual({ action: "next" });
    },
  );

  it("admin: / goes to /admin", () => {
    expect(decideRoute("admin", "/")).toEqual({ action: "redirect", to: "/admin" });
  });

  it.each(["/nurse", "/nurse/x", "/classic", "/classic/anything"])("admin: %s goes to /admin", (path) => {
    expect(decideRoute("admin", path)).toEqual({ action: "redirect", to: "/admin" });
  });

  it("admin: /admin is served", () => {
    expect(decideRoute("admin", "/admin")).toEqual({ action: "next" });
  });

  it.each(["/admin", "/admin/", "/admin/patients"])("nurse: %s is not found", (path) => {
    expect(decideRoute("nurse", path)).toEqual({ action: "not_found" });
  });

  it.each(["/classic", "/classic/", "/classic/anything"])("nurse: the retired %s goes to /nurse", (path) => {
    expect(decideRoute("nurse", path)).toEqual({ action: "redirect", to: "/nurse" });
  });

  it("nurse: a path that only starts like /classic is not redirected", () => {
    expect(decideRoute("nurse", "/classical")).toEqual({ action: "next" });
  });

  it.each(["/", "/nurse", "/administrator", "/nursery"])("nurse: %s is served", (path) => {
    expect(decideRoute("nurse", path)).toEqual({ action: "next" });
  });

  it("admin: a path that only starts like /nurse is not redirected", () => {
    expect(decideRoute("admin", "/nursery")).toEqual({ action: "next" });
  });
});

describe("mode helpers", () => {
  it("parses the build mode, defaulting to nurse", () => {
    expect(parseAppMode("admin")).toBe("admin");
    expect(parseAppMode("nurse")).toBe("nurse");
    expect(parseAppMode(undefined)).toBe("nurse");
    expect(parseAppMode("Admin")).toBe("nurse");
  });

  it("accepts only absolute http(s) console URLs", () => {
    expect(consoleUrl("https://admin.example.org")).toBe("https://admin.example.org/");
    expect(consoleUrl("  ")).toBeNull();
    expect(consoleUrl(undefined)).toBeNull();
    expect(consoleUrl("javascript:alert(1)")).toBeNull();
    expect(consoleUrl("/admin")).toBeNull();
  });
});
