// Container health check target — deliberately does not touch the backend so the probe
// reports on the web process itself.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok" });
}
