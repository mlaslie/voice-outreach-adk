"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { CallRun } from "@/lib/types";

/** Scheduled call runs, polled. The scheduler moves them every minute. */
const POLL_MS = 10_000;

export function useCallRuns() {
  const [runs, setRuns] = useState<CallRun[]>([]);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const res = await api.getCallRuns();
      if (alive.current) setRuns(res.runs);
    } catch {
      // Keep the last good list; the next poll retries.
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [refresh]);

  return { runs, refresh };
}
