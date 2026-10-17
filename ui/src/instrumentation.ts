/**
 * Runs once on the server before anything else is imported.
 *
 * Node 22+ exposes a global `localStorage`, but unless the process was
 * started with `--localstorage-file=<path>` it is a stub object with no
 * methods. Client libraries feature-detect storage with
 * `typeof localStorage !== "undefined"` and then call `.getItem(...)`,
 * which throws during SSR and turns every page into a 500:
 *
 *     TypeError: localStorage.getItem is not a function
 *
 * `typeof window === "undefined"` guards don't help — the library never
 * gets that far, because the presence check passes. Removing the broken
 * global makes those libraries take their server branch, which is what
 * they'd do under any older Node.
 *
 * Only removes it when it's actually broken, so a real Web Storage
 * implementation (or a future Node that ships a working default) is left
 * alone.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const g = globalThis as { localStorage?: { getItem?: unknown } };
  if (g.localStorage && typeof g.localStorage.getItem !== "function") {
    delete g.localStorage;
  }
}
