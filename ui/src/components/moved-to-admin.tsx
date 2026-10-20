import { SlidersHorizontal } from "lucide-react";

/**
 * Where a classic dashboard control used to be. Configuration moved to the
 * admin console (a separate build and service, docs/ADMIN-PLAN.md); the API
 * refuses those writes from this build, so the controls are gone rather than
 * left to fail.
 */
export function MovedToAdmin({ what }: { what?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
      <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
      {what ? `${what} moved` : "Moved"} to the admin console
    </span>
  );
}
