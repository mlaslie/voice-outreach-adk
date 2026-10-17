"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import { CallDoc } from "@/lib/types";

export function useCallsStream() {
  const [calls, setCalls] = useState<Map<string, CallDoc>>(new Map());
  const [connState, setConnState] = useState<"live" | "polling" | "disconnected">(
    "disconnected"
  );
  const [loading, setLoading] = useState(true);

  const esRef = useRef<EventSource | null>(null);
  const errorCountRef = useRef(0);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const upgradeRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const rebuildMap = useCallback((docs: CallDoc[]) => {
    const map = new Map<string, CallDoc>();
    for (const doc of docs) {
      map.set(doc.doc_id, doc);
    }
    return map;
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      const res = await api.getCalls({ limit: 100 });
      if (!mountedRef.current) return;
      setCalls(rebuildMap(res.calls));
      setLoading(false);
    } catch {
      // ignore
    }
  }, [rebuildMap]);

  const refresh = useCallback(() => {
    fetchAll();
  }, [fetchAll]);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    if (upgradeRef.current) {
      clearInterval(upgradeRef.current);
      upgradeRef.current = null;
    }
  }, []);

  const connectSSE = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    // Reset error count on every fresh connection so an old failure
    // count from a previous EventSource can't immediately trip the
    // 60s re-upgrade attempt back into polling.
    errorCountRef.current = 0;

    const es = new EventSource(api.streamUrl());
    esRef.current = es;

    es.addEventListener("init", (e) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        setCalls(rebuildMap(data.calls ?? []));
        setLoading(false);
        setConnState("live");
        errorCountRef.current = 0;
        stopPolling();
      } catch {
        // ignore
      }
    });

    es.addEventListener("call", (e) => {
      if (!mountedRef.current) return;
      try {
        const doc: CallDoc = JSON.parse((e as MessageEvent).data);
        setCalls((prev) => new Map(prev).set(doc.doc_id, doc));
      } catch {
        // ignore
      }
    });

    es.onerror = () => {
      if (!mountedRef.current) return;
      errorCountRef.current += 1;
      if (errorCountRef.current >= 5) {
        es.close();
        esRef.current = null;
        setConnState("polling");
        startPolling();
      } else {
        setConnState("disconnected");
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rebuildMap, stopPolling]);

  const startPolling = useCallback(() => {
    if (pollingRef.current) return;
    fetchAll();
    pollingRef.current = setInterval(fetchAll, 5000);

    // try to re-upgrade every 60 s
    upgradeRef.current = setInterval(() => {
      if (typeof EventSource !== "undefined" && esRef.current === null) {
        connectSSE();
      }
    }, 60000);
  }, [fetchAll, connectSSE]);

  useEffect(() => {
    mountedRef.current = true;

    if (typeof EventSource === "undefined") {
      setConnState("polling");
      startPolling();
    } else {
      connectSSE();
    }

    return () => {
      mountedRef.current = false;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { calls, connState, refresh, loading };
}
