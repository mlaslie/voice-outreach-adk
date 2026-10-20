import { consoleUrl } from "@/lib/mode";
import { AdminApp } from "./admin-app";

// NURSE_CONSOLE_URL is a runtime env var on the service, read per request,
// so the page is never prerendered with the build machine's value. The
// nurse build never serves this page (src/middleware.ts answers 404).
export const dynamic = "force-dynamic";

export default function AdminPage() {
  return <AdminApp nurseConsoleUrl={consoleUrl(process.env.NURSE_CONSOLE_URL)} />;
}
