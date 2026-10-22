"use client";

import { useState } from "react";
import { Download, Languages, Maximize2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TranscriptPanel } from "./transcript-panel";
import { CallDoc, TranscriptTranslation, TranscriptTurn } from "@/lib/types";
import { downloadText, transcriptFilename, transcriptToText } from "@/lib/transcript";
import { formatPhone } from "@/lib/format";
import { useLanguages } from "@/components/languages-provider";
import { languageName } from "@/lib/languages";
import {
  TranscriptView, VIEW_LABELS, sourceLanguage, translationNote, translationState,
} from "@/lib/translation";
import { cn } from "@/lib/utils";

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
  /** The post-call translation (null: not run yet). */
  translation?: TranscriptTranslation | null;
  /** The language the patient mostly spoke. */
  languageUsed?: string | null;
  /** The use case's display title, for the download's title line. */
  title?: string;
}

/** English / Original / Side by side. Shown only for a translated transcript. */
export function ViewToggle({
  value,
  onChange,
  options = ["english", "original", "side"],
}: {
  value: TranscriptView;
  onChange: (v: TranscriptView) => void;
  options?: TranscriptView[];
}) {
  return (
    <div className="inline-flex rounded-full bg-mist p-0.5 text-[12px]" role="radiogroup" aria-label="Transcript language">
      {options.map((v) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cn(
            "rounded-full px-2.5 py-1 font-semibold transition-colors",
            value === v ? "bg-white text-brand-deep shadow-sm" : "text-ink-soft hover:text-ink",
          )}
        >
          {VIEW_LABELS[v]}
        </button>
      ))}
    </div>
  );
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
  translation,
  languageUsed,
  title,
}: TranscriptViewerProps) {
  const [fullscreen, setFullscreen] = useState(false);
  const [view, setView] = useState<TranscriptView>("english");
  const langs = useLanguages()?.languages ?? [];

  const hasTurns = turns.length > 0;
  const state = translationState(turns, translation);
  const translated = state === "translated";
  const source = sourceLanguage(turns, languageUsed);
  const note = translationNote(state, source, langs);
  // Untranslated turns have only their original text.
  const shown: TranscriptView = translated ? view : "original";

  const handleDownload = () => {
    if (!hasTurns) {
      toast.error("Nothing to download yet — the transcript is empty.");
      return;
    }
    const opts = { title, view: shown, languageName: source ? languageName(source, langs) : null };
    downloadText(transcriptFilename(call, translated ? shown : undefined), transcriptToText(call, turns, opts));
    toast.success("Transcript downloaded");
  };

  const languageBar = note ? (
    <div className={cn("mb-2 flex flex-wrap items-center justify-between gap-2", fill && "shrink-0")}>
      <span
        className={cn("inline-flex items-center gap-1.5 text-[12px]", state === "failed" ? "text-brand-red-dark" : "text-ink-soft")}
        title={translated ? "Translated by a machine after the call. The recording and the original words are unchanged." : undefined}
      >
        <Languages className="h-3.5 w-3.5" />
        {note}
      </span>
      {translated ? <ViewToggle value={view} onChange={setView} /> : null}
    </div>
  ) : null;

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

      {languageBar}

      <div className={fill ? "flex-1 min-h-0" : ""}>
        <TranscriptPanel
          view={shown}
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
                Download .txt{translated ? ` (${VIEW_LABELS[shown].toLowerCase()})` : ""}
              </Button>
            </div>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-hidden px-6 py-4 flex flex-col">
            {languageBar}
            <div className="flex-1 min-h-0">
            <TranscriptPanel
              view={shown}
              expanded
              turns={turns}
              loading={loading}
              error={error}
              available={available}
              reason={reason}
              onReload={onReload}
            />
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
