"use client";

import { RefreshCw, Phone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LiveIndicator } from "@/components/live-indicator";

interface DashboardHeaderProps {
  connState: "live" | "polling" | "disconnected";
  onRefresh: () => void;
  onPlaceCall: () => void;
}

export function DashboardHeader({ connState, onRefresh, onPlaceCall }: DashboardHeaderProps) {
  return (
    <div className="flex items-start justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Post-Op Call Center</h1>
        <p className="text-sm text-ink-soft mt-0.5">Outbound wellness-check demo</p>
      </div>
      <div className="flex items-center gap-3">
        <LiveIndicator state={connState} />
        <Button variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCw className="h-4 w-4 mr-1.5" />
          Refresh
        </Button>
        <Button size="sm" onClick={onPlaceCall}>
          <Phone className="h-4 w-4 mr-1.5" />
          Place a Call
        </Button>
      </div>
    </div>
  );
}
