// Runtime proxy to the FastAPI backend.
// API_URLS is read at request time (no rebuild needed) and tried in order, so the UI keeps
// working if Docker's internal DNS can't resolve "api" but the host-published port is reachable.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASES = (process.env.API_URLS || process.env.API_URL || "http://localhost:8000")
  .split(",").map((s) => s.trim().replace(/\/$/, "")).filter(Boolean);
let preferred = 0; // index of the last base that answered

async function proxy(req: NextRequest, { params }: { params: { path: string[] } }) {
  const search = req.nextUrl.search || "";
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.text();
  const headers: Record<string, string> = {};
  for (const h of ["authorization", "content-type", "accept"]) {
    const v = req.headers.get(h);
    if (v) headers[h] = v;
  }
  const order = [preferred, ...BASES.map((_, i) => i).filter((i) => i !== preferred)];
  const errors: string[] = [];
  for (const i of order) {
    const url = `${BASES[i]}/api/${params.path.map(encodeURIComponent).join("/")}${search}`;
    try {
      const res = await fetch(url, { method: req.method, headers, body, cache: "no-store", signal: AbortSignal.timeout(120_000) });
      preferred = i;
      return new Response(await res.arrayBuffer(), {
        status: res.status,
        headers: { "content-type": res.headers.get("content-type") || "application/json" },
      });
    } catch (e) {
      errors.push(`${BASES[i]}: ${(e as Error & { cause?: { code?: string } }).cause?.code || (e as Error).message}`);
    }
  }
  console.error("API unreachable:", errors.join(" | "));
  return Response.json({ detail: `QueryPilot API unreachable (${errors.join("; ")}). Is the api container running?` }, { status: 502 });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
