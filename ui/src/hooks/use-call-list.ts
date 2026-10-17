"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { CallListEntry, CallListRun } from "@/lib/types";

/** Poll cadence while a run is in progress. */
const RUN_POLL_MS = 3000;
/**
 * Idle cadence. Entries change without a run on this page: the scheduler
 * dials them, the SMS survey completes them, a report clears them. With no
 * idle poll a row sat on "Calling now" until the page was reloaded.
 */
const IDLE_POLL_MS = 10_000;

export function useCallList() {
  const [entries, setEntries] = useState<CallListEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<CallListRun | null>(null);

  const mountedRef = useRef(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const refreshEntries = useCallback(async () => {
    try {
      const res = await api.getCallList();
      if (!mountedRef.current) return;
      setEntries(res.entries);
      setError(null);
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load the call list");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  const refreshRun = useCallback(async () => {
    try {
      const res = await api.getCallListRun();
      if (!mountedRef.current) return;
      setRun(res.run);
      return res.run;
    } catch {
      // A failed run poll is not worth surfacing; the next tick retries.
      return null;
    }
  }, []);

  // While a run is active, poll both the run state and the entries so
  // per-patient status (calling / attempts / last outcome) stays live.
  const isRunning = run?.status === "running";
  const wasRunningRef = useRef(false);
  useEffect(() => {
    if (!isRunning) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // The poll that observed "completed" fetched entries concurrently, so
      // it can miss the runner's very last write. Re-read once on the way out
      // or the final patient can sit on "Calling…" until a manual refresh.
      if (wasRunningRef.current) {
        wasRunningRef.current = false;
        refreshEntries();
      }
      return;
    }
    wasRunningRef.current = true;
    if (pollRef.current) return;
    pollRef.current = setInterval(() => {
      refreshRun();
      refreshEntries();
    }, RUN_POLL_MS);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isRunning, refreshRun, refreshEntries]);

  // Initial load: entries plus any run left over from a previous visit.
  useEffect(() => {
    refreshEntries();
    refreshRun();
  }, [refreshEntries, refreshRun]);

  // Idle poll (the run poll above takes over while a run is active).
  useEffect(() => {
    if (isRunning) return;
    const id = setInterval(() => {
      refreshEntries();
      refreshRun();
    }, IDLE_POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, refreshEntries, refreshRun]);

  return { entries, loading, error, run, refreshEntries, refreshRun };
}
