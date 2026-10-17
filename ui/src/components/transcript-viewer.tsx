"use client";

import { useState } from "react";
import { Download, Maximize2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TranscriptPanel } from "./transcript-panel";
import { CallDoc, TranscriptTurn } from "@/lib/types";
import { downloadText, transcriptFilename, transcriptToText } from "@/lib/transcript";
import { formatPhone } from "@/lib/format";

interface TranscriptViewerProps {
  call: CallDoc | null | undefined;
  turns: TranscriptTurn[];
  loading: boolean;
  error: string | null;
  available: boolean | null;
  reason?: string;
  onReload: () => void;
  /** Stretch to the parent's height instead of the default capped panel. */
  fill?: boolean;
}

export function TranscriptViewer({
  call,
  turns,
  loading,
  error,
  available,
  reason,
  onReload,
  fill = false,
}: TranscriptViewerProps) {
  const [fullscreen, setFullscreen] = useState(false);

  const hasTurns = turns.length > 0;

  const handleDownload = () => {
    if (!hasTurns) {
      toast.error("Nothing to download yet — the transcript is empty.");
      return;
    }
    downloadText(transcriptFilename(call), transcriptToText(call, turns));
    toast.success("Transcript downloaded");
  };

  const toolbar = (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" onClick={onReload} title="Reload transcript">
        <RefreshCw className="h-3.5 w-3.5" />
        <span className="sr-only">Reload</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleDownload}
        disabled={!hasTurns}
        title="Download as .txt"
      >
        <Download className="h-3.5 w-3.5" />
        <span className="sr-only">Download</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setFullscreen(true)}
        disabled={!hasTurns}
        title="Expand to full screen"
      >
        <Maximize2 className="h-3.5 w-3.5" />
        <span className="sr-only">Expand</span>
      </Button>
    </div>
  );

  return (
    <>
      <div className={`flex justify-between items-center mb-3 ${fill ? "shrink-0" : ""}`}>
        <span className="text-sm font-medium">Transcript</span>
        {toolbar}
      </div>

      <div className={fill ? "flex-1 min-h-0" : ""}>
        <TranscriptPanel
          expanded={fill}
          turns={turns}
          loading={loading}
          error={error}
          available={available}
          reason={reason}
          onReload={onReload}
        />
      </div>

      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="max-w-none w-[96vw] h-[92vh] top-[4vh] translate-y-0 flex flex-col gap-0 p-0">
          <DialogHeader className="px-6 pt-6 pb-4 border-b shrink-0">
            <DialogTitle className="pr-10">
              Transcript
              {call?.patient_name && ` — ${call.patient_name}`}
            </DialogTitle>
            <p className="text-sm text-ink-soft">
              {call?.to && formatPhone(call.to)}
              {call?.doc_id && ` · ${call.doc_id}`}
              {` · ${turns.length} turn${turns.length === 1 ? "" : "s"}`}
            </p>
            <div className="pt-2">
              <Button variant="outline" size="sm" onClick={handleDownload} disabled={!hasTurns}>
                <Download className="h-3.5 w-3.5 mr-1.5" />
                Download .txt
              </Button>
            </div>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-hidden px-6 py-4">
            <TranscriptPanel
              expanded
              turns={turns}
              loading={loading}
              error={error}
              available={available}
              reason={reason}
              onReload={onReload}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
