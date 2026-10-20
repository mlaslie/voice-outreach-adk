"use client";

import { useState } from "react";
import { toast } from "sonner";
import { ExternalLink, Headphones, MessageSquareText } from "lucide-react";
import { AdminActions } from "@/lib/nurse/actions";
import { Panel } from "@/components/nurse/parts";
import { SmsTestDialog } from "@/components/sms-test-dialog";

/**
 * Admin → Testing: talk to the agent from this browser, or play the patient
 * in the text follow-up. Both use fictional data and no phone line; the
 * backend refuses both in production.
 */

const BUTTON = "ml-auto inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-ink-soft ring-1 ring-black/[0.08] hover:text-ink disabled:opacity-50";

export function TestInBrowserPanel({ actions }: { actions: Pick<AdminActions, "live" | "startTestCall"> }) {
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<{ url: string; until: number } | null>(null);

  const start = async () => {
    if (busy) return;
    if (!actions.live) {
      await actions.startTestCall(); // explains that sample data sends nothing
      return;
    }
    // Open the tab now, inside the click, so a popup blocker allows it; the
    // softphone address arrives after the request.
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    setBusy(true);
    try {
      const res = await actions.startTestCall();
      if (!res) {
        tab?.close();
        return;
      }
      setLast({ url: res.softphone_url, until: Date.now() + res.expires_in * 1000 });
      if (tab) tab.location.href = res.softphone_url;
      else toast.info("The browser blocked the new tab. Use the link on this card.");
    } catch (e) {
      tab?.close();
      toast.error(e instanceof Error ? e.message : "Could not start a test call.");
    } finally {
      setBusy(false);
    }
  };

  const fresh = last && last.until > Date.now();

  return (
    <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand/10 text-brand-deep">
        <Headphones className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="font-display text-[15px] font-semibold text-ink">Test in browser</h3>
        <p className="text-[12px] text-ink-muted">
          Talk to the voice agent from this browser, with your microphone, as the fictional test patient. No phone line is
          used and no real patient is involved. The softphone opens in a new tab; its link works for a few minutes.
        </p>
        {fresh && last ? (
          <a href={last.url} target="_blank" rel="noopener noreferrer"
             className="mt-1 inline-flex items-center gap-1 text-[12px] font-semibold text-brand-dark hover:underline">
            Open the softphone again <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      <button type="button" onClick={start} disabled={busy} className={BUTTON}>
        <Headphones className="h-3.5 w-3.5" /> {busy ? "Starting…" : "Test in browser"}
      </button>
    </Panel>
  );
}

/** Moved here from Messaging. */
export function TestByTextPanel({ live }: { live: boolean }) {
  const [open, setOpen] = useState(false);
  const start = () => {
    if (!live) {
      toast.info("Sample data: the text test runs against the live backend only. Switch to live data to try it.");
      return;
    }
    setOpen(true);
  };
  return (
    <>
      <Panel className="flex flex-wrap items-center gap-3 px-5 py-3.5">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand/10 text-brand-deep">
          <MessageSquareText className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold text-ink">Test by text</h3>
          <p className="text-[12px] text-ink-muted">
            Play the patient in the text follow-up, end to end. Dry run: nothing is sent to a phone.
          </p>
        </div>
        <button type="button" onClick={start} className={BUTTON}>
          <MessageSquareText className="h-3.5 w-3.5" /> Test by text
        </button>
      </Panel>
      {live ? <SmsTestDialog open={open} onOpenChange={setOpen} /> : null}
    </>
  );
}
