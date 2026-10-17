"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface RecordingPlayerProps {
  callSid: string;
  hasRecording: boolean;
}

export function RecordingPlayer({ callSid, hasRecording }: RecordingPlayerProps) {
  const [error, setError] = useState(false);

  if (!hasRecording) {
    return <p className="text-sm text-ink-soft">No recording for this call.</p>;
  }

  if (error) {
    return (
      <p className="text-sm text-ink-soft">
        Recording not available yet — GE CX may still be writing it.
      </p>
    );
  }

  return (
    <audio
      controls
      preload="none"
      src={api.recordingUrl(callSid)}
      className="w-full"
      onError={() => setError(true)}
    />
  );
}
