import { describe, expect, it } from "vitest";
import {
  ancestors,
  childPointer,
  containerPointers,
  dottedToPointer,
  formatPointer,
  isSelfOrAncestor,
  parsePointer,
  pointerExists,
  resolvePointer,
} from "./json-pointer";

const DOC = {
  a: { "b/c": [10, { "m~n": "x" }] },
  list: [],
  nil: null,
  "": "empty key",
};

describe("parse and format", () => {
  it("parses tokens and unescapes ~1 and ~0 (in that order)", () => {
    expect(parsePointer("")).toEqual([]);
    expect(parsePointer("/a/b~1c/1/m~0n")).toEqual(["a", "b/c", "1", "m~n"]);
    expect(parsePointer("/~01")).toEqual(["~1"]);
    expect(parsePointer("/")).toEqual([""]);
  });
  it("refuses a pointer without a leading slash", () => {
    expect(() => parsePointer("a/b")).toThrow();
  });
  it("formats tokens back (round trip)", () => {
    const toks = ["a", "b/c", 1, "m~n"];
    expect(formatPointer(toks)).toBe("/a/b~1c/1/m~0n");
    expect(parsePointer(formatPointer(toks))).toEqual(["a", "b/c", "1", "m~n"]);
    expect(childPointer("/a", "b/c")).toBe("/a/b~1c");
  });
});

describe("resolve", () => {
  it("finds values, including falsy ones and the empty key", () => {
    expect(resolvePointer(DOC, "")).toBe(DOC);
    expect(resolvePointer(DOC, "/a/b~1c/0")).toBe(10);
    expect(resolvePointer(DOC, "/a/b~1c/1/m~0n")).toBe("x");
    expect(resolvePointer(DOC, "/nil")).toBeNull();
    expect(resolvePointer(DOC, "/")).toBe("empty key");
  });
  it("is undefined for missing paths and bad indices, never throwing", () => {
    expect(resolvePointer(DOC, "/a/b~1c/2")).toBeUndefined();
    expect(resolvePointer(DOC, "/a/b~1c/01")).toBeUndefined();
    expect(resolvePointer(DOC, "/a/b~1c/-")).toBeUndefined();
    expect(resolvePointer(DOC, "/nil/x")).toBeUndefined();
    expect(resolvePointer(DOC, "/toString")).toBeUndefined();
  });
  it("tells existence apart from a null value", () => {
    expect(pointerExists(DOC, "/nil")).toBe(true);
    expect(pointerExists(DOC, "/missing")).toBe(false);
    expect(pointerExists(DOC, "/a/b~1c/1")).toBe(true);
    expect(pointerExists(DOC, "/a/b~1c/5")).toBe(false);
    expect(pointerExists(DOC, "bad")).toBe(false);
  });
});

describe("ancestors (auto-expand)", () => {
  it("lists every container to open, root first", () => {
    expect(ancestors("/entry/0/resource/item/3")).toEqual(["", "/entry", "/entry/0", "/entry/0/resource", "/entry/0/resource/item"]);
    expect(ancestors("")).toEqual([]);
    expect(ancestors("/a~1b/c")).toEqual(["", "/a~1b"]);
  });
  it("knows containment by whole tokens", () => {
    expect(isSelfOrAncestor("/a", "/a/b")).toBe(true);
    expect(isSelfOrAncestor("/a", "/a")).toBe(true);
    expect(isSelfOrAncestor("/a", "/ab")).toBe(false);
    expect(isSelfOrAncestor("", "/x")).toBe(true);
    expect(isSelfOrAncestor("/a/b", "/a")).toBe(false);
  });
  it("lists every object and array for expand-all", () => {
    expect(containerPointers(DOC).sort()).toEqual(["", "/a", "/a/b~1c", "/a/b~1c/1", "/list"].sort());
    expect(containerPointers(5)).toEqual([]);
  });
});

describe("validation paths as pointers", () => {
  it("reads both API styles", () => {
    expect(dottedToPointer("use_cases[0].questionnaire_response.items.pain_trend")).toBe("/use_cases/0/questionnaire_response/items/pain_trend");
    expect(dottedToPointer("use_cases.0.questionnaire_response.items.pain_trend.codes")).toBe("/use_cases/0/questionnaire_response/items/pain_trend/codes");
    expect(dottedToPointer("use_cases[0].observations[1]")).toBe("/use_cases/0/observations/1");
    expect(dottedToPointer("profile")).toBe("");
    expect(dottedToPointer("/entry/0")).toBe("/entry/0");
  });
});
