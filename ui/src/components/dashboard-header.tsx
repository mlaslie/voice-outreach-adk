"use client";

import Link from "next/link";
import { RefreshCw, Phone, Headphones, LayoutDashboard } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { LiveIndicator } from "@/components/live-indicator";
import { SmsIndicator } from "@/components/sms-indicator";
import { MovedToAdmin } from "@/components/moved-to-admin";

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
        {/* "Test by text" (an admin route) moved with the rest of the configuration. */}
        <p className="mt-1"><MovedToAdmin what="Test by text and the allowed numbers" /></p>
      </div>
      <div className="flex items-center gap-3">
        <SmsIndicator />
        <LiveIndicator state={connState} />
        <Link href="/nurse">
          <Button variant="outline" size="sm" tabIndex={-1}>
            <LayoutDashboard className="h-4 w-4 mr-1.5" />
            Nurse console
          </Button>
        </Link>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCw className="h-4 w-4 mr-1.5" />
          Refresh
        </Button>
        <Button
          variant="outline"
          size="sm"
          title="Talk to the agent from this browser as the fictional test patient (no phone line)"
          onClick={async () => {
            try {
              const { softphone_url } = await api.startTestCall();
              window.open(softphone_url, "_blank", "noopener");
            } catch (e) {
              toast.error(e instanceof Error ? e.message : "Could not start a test call");
            }
          }}
        >
          <Headphones className="h-4 w-4 mr-1.5" />
          Test in browser
        </Button>
        <Button size="sm" onClick={onPlaceCall}>
          <Phone className="h-4 w-4 mr-1.5" />
          Place a Call
        </Button>
      </div>
    </div>
  );
}
