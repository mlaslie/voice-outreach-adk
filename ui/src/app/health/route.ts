// Liveness/startup probe target for Cloud Run. No backend call: a slow API must
// not make the dashboard look dead.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ ok: true });
}
