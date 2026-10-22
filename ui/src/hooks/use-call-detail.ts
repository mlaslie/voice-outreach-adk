"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { api, ApiError } from "@/lib/api";
import { CallDoc, TranscriptResponse, TranscriptTurn } from "@/lib/types";

export function useCallDetail(callSid: string | null) {
  const [call, setCall] = useState<CallDoc | null>(null);
  const [loading, setLoading] = useState(false);

  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptAvailable, setTranscriptAvailable] = useState<boolean | null>(null);
  const [transcriptReason, setTranscriptReason] = useState<string | undefined>(undefined);
  // Bilingual transcripts (docs/LANGUAGES-PLAN.md step 2).
  const [transcriptMeta, setTranscriptMeta] = useState<
    Pick<TranscriptResponse, "translation" | "language_used" | "language" | "title">
  >({});

  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reloadTranscript = useCallback(async () => {
    if (!callSid) return;
    setTranscriptLoading(true);
    setTranscriptError(null);
    try {
      const res = await api.getTranscript(callSid);
      if (!mountedRef.current) return;
      setTranscriptAvailable(res.available);
      setTranscriptReason(res.reason);
      setTurns(res.turns);
      setTranscriptMeta({
        translation: res.translation ?? null,
        language_used: res.language_used ?? null,
        language: res.language ?? null,
        title: res.title,
      });
    } catch (err) {
      if (!mountedRef.current) return;
      const msg = err instanceof ApiError ? err.message : "Failed to load transcript";
      setTranscriptError(msg);
    } finally {
      if (mountedRef.current) {
        setTranscriptLoading(false);
      }
    }
  }, [callSid]);

  useEffect(() => {
    if (!callSid) {
      setCall(null);
      setTurns([]);
      setTranscriptAvailable(null);
      setTranscriptError(null);
      setTranscriptReason(undefined);
      setTranscriptMeta({});
      return;
    }

    let cancelled = false;
    setLoading(true);
    setCall(null);
    setTurns([]);
    setTranscriptAvailable(null);
    setTranscriptMeta({});

    api.getCall(callSid).then((doc) => {
      if (cancelled) return;
      setCall(doc);
      setLoading(false);
    }).catch(() => {
      if (cancelled) return;
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [callSid]);

  return {
    call,
    loading,
    turns,
    transcriptLoading,
    transcriptError,
    transcriptAvailable,
    transcriptReason,
    transcriptMeta,
    reloadTranscript,
  };
}
