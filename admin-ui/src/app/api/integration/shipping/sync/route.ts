// src/app/api/integration/shipping/sync/route.ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = (process.env.INTEGRATION_BASE_URL || "http://integration:8000").replace(/\/+$/, "");
const USER = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || "";
const PASS = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || "";

type Tried = { url: string; status?: number; ok?: boolean; bodyPreview?: string; error?: string };

function authHeader(): string | undefined {
  if (!USER || !PASS) return undefined;
  return "Basic " + Buffer.from(`${USER}:${PASS}`).toString("base64");
}

function candidateUrls(): string[] {
  return [
    `${BASE}/admin/api/integration/shipping/sync`,
    `${BASE}/admin/api/shipping/sync`,
    `${BASE}/api/integration/shipping/sync`,
    `${BASE}/api/shipping/sync`,
    `${BASE}/integration/shipping/sync`,
    `${BASE}/shipping/sync`,
  ];
}

function previewBody(s: string, n = 200) {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n) + "…";
}

export async function POST(req: NextRequest) {
  const payload = await req.json().catch(() => ({} as any));
  const tried: Tried[] = [];

  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  const a = authHeader();
  if (a) headers["Authorization"] = a;

  for (const url of candidateUrls()) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        cache: "no-store",
      });
      const text = await r.text().catch(() => "");
      const attempt: Tried = { url, status: r.status, ok: r.ok, bodyPreview: previewBody(text) };

      if (r.ok) {
        try {
          const data = text ? JSON.parse(text) : {};
          return NextResponse.json(data, { status: r.status, headers: { "Cache-Control": "no-store" } });
        } catch {
          attempt.error = "Non-JSON response despite 2xx";
          tried.push(attempt);
          continue;
        }
      }

      tried.push(attempt);
      continue;
    } catch (e: any) {
      tried.push({ url, error: String(e?.message || e) });
      continue;
    }
  }

  return NextResponse.json(
    { error: "Not Found at integration", tried },
    { status: 502 }
  );
}
