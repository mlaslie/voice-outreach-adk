"use client";

import { useCallback, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { api, type SmsControlAction, type SmsControlStatus } from "@/lib/api";
import { SMS_CONTROL_POLL_MS, demoSmsControl, flipLocally } from "@/lib/sms-control";

/**
 * One shared copy of the SMS switch status per mode, so the top-right pill
 * and the Settings card always agree. Polls every 30 s while anything shows
 * it, and updates right after a flip (the POST returns the re-read number).
 *
 * "demo" never calls the API: fictional local state, flipped in this tab only.
 */
export type SmsControlMode = "live" | "demo";

interface Snapshot {
  status: SmsControlStatus | null;
  /** The last read failed (the last good status, if any, is kept). */
  error: string | null;
}

interface Source {
  read(fresh: boolean): Promise<SmsControlStatus>;
  flip(action: SmsControlAction): Promise<SmsControlStatus>;
}

class SmsControlStore {
  private snap: Snapshot = { status: null, error: null };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private seq = 0;

  constructor(private readonly source: Source) {}

  get = (): Snapshot => this.snap;

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    if (this.listeners.size === 1) {
      void this.load(false);
      this.timer = setInterval(() => void this.load(false), SMS_CONTROL_POLL_MS);
    }
    return () => {
      this.listeners.delete(fn);
      if (!this.listeners.size && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  };

  private set(next: Snapshot) {
    this.snap = next;
    this.listeners.forEach((l) => l());
  }

  load = async (fresh: boolean): Promise<void> => {
    const n = ++this.seq;
    try {
      const status = await this.source.read(fresh);
      if (n === this.seq) this.set({ status, error: null });
    } catch (e) {
      if (n === this.seq) this.set({ ...this.snap, error: e instanceof Error ? e.message : "Could not read the SMS number." });
    }
  };

  /** Throws the backend's error (409/502 detail) for the caller to show. */
  flip = async (action: SmsControlAction): Promise<SmsControlStatus> => {
    const n = ++this.seq; // a poll already in flight must not overwrite the flip
    const status = await this.source.flip(action);
    if (n === this.seq) this.set({ status, error: null });
    return status;
  };
}

const liveStore = new SmsControlStore({
  read: (fresh) => api.getSmsControl(fresh),
  flip: (action) => api.setSmsControl(action),
});

let demoState: SmsControlStatus | null = null;
const demoStore = new SmsControlStore({
  read: async () => (demoState ??= demoSmsControl(new Date().toISOString())),
  flip: async (action) => {
    const now = new Date().toISOString();
    demoState = flipLocally(demoState ?? demoSmsControl(now), action, now);
    toast.info("Sample data: nothing is changed. The switch moved in this tab only.");
    return demoState;
  },
});

const EMPTY: Snapshot = { status: null, error: null };

export function useSmsControl(mode: SmsControlMode) {
  const store = mode === "demo" ? demoStore : liveStore;
  const snap = useSyncExternalStore(store.subscribe, store.get, () => EMPTY);
  const refresh = useCallback((fresh = true) => store.load(fresh), [store]);
  const flip = useCallback((action: SmsControlAction) => store.flip(action), [store]);
  return { ...snap, refresh, flip };
}
