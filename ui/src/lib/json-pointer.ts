/**
 * JSON Pointers (RFC 6901), as the EHR export preview's source map uses them
 * ("/questionnaire_response/item/4/answer" -> "/entry/0/resource/item/3/answer/0").
 * Pure helpers for the JSON tree (components/json-tree.tsx): parse, format,
 * resolve, and the ancestors to expand so a node can be scrolled into view.
 */

export type Pointer = string;

/** "/a/b~1c/0" -> ["a", "b/c", "0"]; "" (the whole document) -> []. Throws on a malformed pointer. */
export function parsePointer(p: Pointer): string[] {
  if (p === "") return [];
  if (!p.startsWith("/")) throw new Error(`not a JSON pointer: ${JSON.stringify(p)}`);
  return p
    .slice(1)
    .split("/")
    .map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
}

/** The inverse of parsePointer: ["a", "b/c", 0] -> "/a/b~1c/0". */
export function formatPointer(tokens: ReadonlyArray<string | number>): Pointer {
  return tokens.map((t) => "/" + String(t).replace(/~/g, "~0").replace(/\//g, "~1")).join("");
}

/** The child pointer of `parent` for one key or index. */
export function childPointer(parent: Pointer, key: string | number): Pointer {
  return parent + formatPointer([key]);
}

/** The value at `p`, or undefined when the path does not exist (never throws on a well-formed pointer). */
export function resolvePointer(doc: unknown, p: Pointer): unknown {
  let cur: unknown = doc;
  for (const t of parsePointer(p)) {
    if (Array.isArray(cur)) {
      if (!/^(0|[1-9][0-9]*)$/.test(t)) return undefined;
      const i = Number(t);
      if (i >= cur.length) return undefined;
      cur = cur[i];
    } else if (cur !== null && typeof cur === "object") {
      if (!Object.prototype.hasOwnProperty.call(cur, t)) return undefined;
      cur = (cur as Record<string, unknown>)[t];
    } else {
      return undefined;
    }
  }
  return cur;
}

/** Whether `p` names an existing node of `doc`. */
export function pointerExists(doc: unknown, p: Pointer): boolean {
  try {
    if (p === "") return true;
    const toks = parsePointer(p);
    const parent = resolvePointer(doc, formatPointer(toks.slice(0, -1)));
    const last = toks[toks.length - 1];
    if (Array.isArray(parent)) return /^(0|[1-9][0-9]*)$/.test(last) && Number(last) < parent.length;
    return parent !== null && typeof parent === "object" && Object.prototype.hasOwnProperty.call(parent, last);
  } catch {
    return false;
  }
}

/** The proper ancestors of `p`, root first: "/a/b/c" -> ["", "/a", "/a/b"]. These are the nodes to expand. */
export function ancestors(p: Pointer): Pointer[] {
  const toks = parsePointer(p);
  const out: Pointer[] = [];
  for (let i = 0; i < toks.length; i++) out.push(formatPointer(toks.slice(0, i)));
  return out;
}

/** Whether `a` is `b` or contains it ("/a" contains "/a/b", but not "/ab"). */
export function isSelfOrAncestor(a: Pointer, b: Pointer): boolean {
  return a === b || a === "" || b.startsWith(a + "/");
}

/** Every container (object or array) pointer in `doc`, root included: "expand all". */
export function containerPointers(doc: unknown, base: Pointer = ""): Pointer[] {
  if (doc === null || typeof doc !== "object") return [];
  const out: Pointer[] = [base];
  const entries: [string | number, unknown][] = Array.isArray(doc)
    ? doc.map((v, i) => [i, v])
    : Object.entries(doc as Record<string, unknown>);
  for (const [k, v] of entries) out.push(...containerPointers(v, childPointer(base, k)));
  return out;
}

/**
 * A validation path as a JSON Pointer. The API reports two styles: profile
 * checks ("use_cases[0].questionnaire_response.items.pain_trend") and model
 * errors ("use_cases.0.questionnaire_response.items.pain_trend"). Both become
 * "/use_cases/0/questionnaire_response/items/pain_trend". A pointer is kept as is.
 */
export function dottedToPointer(path: string): Pointer {
  if (path === "" || path === "profile") return "";
  if (path.startsWith("/")) return path;
  const toks = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter((t) => t !== "");
  return formatPointer(toks);
}
