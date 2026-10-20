import { useSyncExternalStore } from "react";

const noop = () => () => {};

/** False during the server render and hydration, true in the browser after it.
 *  Sample data uses times relative to "now"; rendering it only in the browser
 *  avoids a server/client mismatch when the two renders land a minute apart. */
export function useMounted(): boolean {
  return useSyncExternalStore(noop, () => true, () => false);
}
