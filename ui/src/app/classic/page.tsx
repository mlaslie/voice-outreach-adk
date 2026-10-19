"use client";

import { useEffect, useState, useMemo } from "react";
import { api } from "@/lib/api";
import { setTimeFormat, useTimeFormat } from "@/lib/time";
import { useCallsStream } from "@/hooks/use-calls-stream";
import { DashboardHeader } from "@/components/dashboard-header";
import { ActiveCallsStrip } from "@/components/active-calls-strip";
import { CallsTable } from "@/components/calls-table";
import { CallListView } from "@/components/call-list-view";
import { FollowUpBanner } from "@/components/follow-up-banner";
import { PlaceCallDialog } from "@/components/place-call-dialog";
import { CallHud } from "@/components/call-hud";
import { FollowUpsView } from "@/components/followups-view";
import { followUpCalls, isActive } from "@/lib/status";
import { CallDoc } from "@/lib/types";

type View = "calls" | "list" | "followups";

export default function DashboardPage() {
  const { calls, connState, refresh, loading } = useCallsStream();
  // Staff times follow the clinic's 12h/24h setting; on failure keep the default.
  useTimeFormat();
  useEffect(() => {
    api.getCallSettings().then((s) => setTimeFormat(s.display?.time_format ?? "24h")).catch(() => {});
  }, []);
  const [view, setView] = useState<View>("calls");
  const [selectedCallSid, setSelectedCallSid] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [followUpOnly, setFollowUpOnly] = useState(false);
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");
  const [placeCallOpen, setPlaceCallOpen] = useState(false);

  const callList = useMemo(() => [...calls.values()], [calls]);

  const activeCalls = useMemo(() => callList.filter(isActive), [callList]);

  const filteredCalls = useMemo(() => {
    let list = callList;
    if (statusFilter === "active") {
      list = list.filter(isActive);
    } else if (statusFilter !== "all") {
      list = list.filter((c) => c.call_status === statusFilter);
    }
    if (followUpOnly) {
      // Latest-call-per-patient, so the row count matches the banner count
      // and a superseded urgent flag can't linger after a clean redial.
      const outstanding = new Set(followUpCalls(callList).map((c) => c.doc_id));
      list = list.filter((c) => outstanding.has(c.doc_id));
    }
    list = [...list].sort((a, b) => {
      const da = new Date(a.initiated_at).getTime();
      const db = new Date(b.initiated_at).getTime();
      return sortDir === "desc" ? db - da : da - db;
    });
    return list;
  }, [callList, statusFilter, followUpOnly, sortDir]);

  const selectedDoc: CallDoc | undefined = selectedCallSid
    ? calls.get(selectedCallSid)
    : undefined;

  // Jumping to follow-ups from the banner should never land on an empty
  // table because an unrelated status filter is still applied.
  function toggleFollowUp() {
    setFollowUpOnly((prev) => {
      const next = !prev;
      if (next) {
        setView("calls");
        setStatusFilter("all");
      }
      return next;
    });
  }

  return (
    <main className="min-h-screen bg-mist pb-16">
      <div className="mx-auto max-w-7xl px-4 py-6 space-y-6">
        <DashboardHeader
          connState={connState}
          onRefresh={refresh}
          onPlaceCall={() => setPlaceCallOpen(true)}
        />

        <FollowUpBanner
          calls={callList}
          active={followUpOnly}
          onToggle={toggleFollowUp}
        />

        {/* View switch */}
        <div className="flex gap-1 border-b">
          {(
            [
              ["calls", "Call Record"],
              ["list", "Call List"],
              ["followups", "Follow-ups"],
            ] as [View, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setView(key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                view === key
                  ? "border-brand text-brand-dark"
                  : "border-transparent text-ink-soft hover:text-ink"
              }`}
            >
              {label}
              {key === "calls" && activeCalls.length > 0 && (
                <span className="ml-2 rounded-full bg-brand-turquoise/25 text-brand-deep px-1.5 py-0.5 text-xs">
                  {activeCalls.length}
                </span>
              )}
            </button>
          ))}
        </div>

        {view === "calls" ? (
          <>
            {activeCalls.length > 0 && (
              <ActiveCallsStrip calls={activeCalls} onSelect={setSelectedCallSid} />
            )}

            <CallsTable
              calls={filteredCalls}
              loading={loading}
              filter={statusFilter}
              sortDir={sortDir}
              followUpOnly={followUpOnly}
              onFilterChange={setStatusFilter}
              onSortChange={setSortDir}
              onFollowUpOnlyChange={setFollowUpOnly}
              onSelect={setSelectedCallSid}
              onDeleted={refresh}
            />
          </>
        ) : view === "list" ? (
          <CallListView />
        ) : (
          <FollowUpsView />
        )}
      </div>

      <PlaceCallDialog
        open={placeCallOpen}
        onOpenChange={setPlaceCallOpen}
        onPlaced={(sid) => {
          setSelectedCallSid(sid);
          setPlaceCallOpen(false);
        }}
      />

      <CallHud
        callSid={selectedCallSid}
        liveDoc={selectedDoc}
        onOpenChange={(open) => {
          if (!open) setSelectedCallSid(null);
        }}
      />
    </main>
  );
}
