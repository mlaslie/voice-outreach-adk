"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FollowUpSession,
  ScheduledCallback,
  fetchCallbacks,
  fetchSessions,
} from "@/lib/followups";

/**
 * Sessions + scheduled callbacks, polled.
 *
 * Polling rather than SSE: the follow-up service has no stream endpoint,
 * and this data changes on the ticker's one-minute cadence, so a 10s poll
 * is already finer-grained than the thing it is watching.
 *
 * Both requests are issued together and applied together, so the summary
 * counts can never be drawn from two different moments.
 */
const POLL_MS = 10_000;

export function useFollowUps() {
  const [sessions, setSessions] = useState<FollowUpSession[]>([]);
  const [callbacks, setCallbacks] = useState<ScheduledCallback[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([fetchSessions(), fetchCallbacks()]);
      if (!alive.current) return;
      setSessions(s.sessions ?? []);
      setCallbacks(c.callbacks ?? []);
      setError(null);
    } catch (e) {
      if (!alive.current) return;
      // Keep the last good data on screen. A blank panel reads as "nothing
      // to do", which is the opposite of what a failed fetch means.
      setError(e instanceof Error ? e.message : "Could not load follow-ups.");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load]);

  return { sessions, callbacks, loading, error, refresh: load };
}
