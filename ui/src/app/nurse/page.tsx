import { consoleUrl } from "@/lib/mode";
import { NurseApp } from "./nurse-app";

// ADMIN_CONSOLE_URL is a runtime env var on the service, read per request,
// so the page is never prerendered with the build machine's value.
export const dynamic = "force-dynamic";

export default function NursePage() {
  return <NurseApp adminConsoleUrl={consoleUrl(process.env.ADMIN_CONSOLE_URL)} />;
}
