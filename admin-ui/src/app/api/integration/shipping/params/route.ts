// src/app/api/integration/shipping/params/route.ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = (process.env.INTEGRATION_BASE_URL || "http://integration:8000").replace(/\/+$/, "");
const USER = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || "";
const PASS = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || "";

type Tried = {
    url: string;
    status?: number;
    ok?: boolean;
    contentType?: string | null;
    bodyPreview?: string;
    error?: string;
};

function authHeader(): string | undefined {
    if (!USER || !PASS) return undefined;
    return "Basic " + Buffer.from(`${USER}:${PASS}`).toString("base64");
}

function headers(json: boolean) {
    const h: Record<string, string> = { Accept: "application/json" };
    if (json) h["Content-Type"] = "application/json";
    const a = authHeader();
    if (a) h["Authorization"] = a;
    return h;
}

function previewBody(s: string, n = 200) {
    const t = s.replace(/\s+/g, " ").trim();
    return t.length <= n ? t : t.slice(0, n) + "…";
}

function candidateUrls(): string[] {
    // Try admin-protected and public variants, with and without /integration
    return [
        `${BASE}/admin/api/integration/shipping/params`,
        `${BASE}/admin/api/shipping/params`,
        `${BASE}/api/integration/shipping/params`,
        `${BASE}/api/shipping/params`,
        `${BASE}/integration/shipping/params`,
        `${BASE}/shipping/params`,
    ];
}

/* ---------------------- GET ---------------------- */
export async function GET() {
    const tried: Tried[] = [];
    for (const url of candidateUrls()) {
        try {
            const r = await fetch(url, { headers: headers(false), cache: "no-store" });
            const text = await r.text().catch(() => "");
            const ct = r.headers.get("content-type");
            const attempt: Tried = {
                url,
                status: r.status,
                ok: r.ok,
                contentType: ct,
                bodyPreview: previewBody(text),
            };

            // If OK, try to JSON-parse and return pass-through
            if (r.ok) {
                try {
                    const data = text ? JSON.parse(text) : {};
                    return NextResponse.json(data, {
                        status: r.status,
                        headers: { "Cache-Control": "no-store" },
                    });
                } catch {
                    attempt.error = "Non-JSON response despite 2xx";
                    tried.push(attempt);
                    continue;
                }
            }

            tried.push(attempt);
            // For 401/403/404 keep trying the next candidate
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

/* ---------------------- POST ---------------------- */
export async function POST(req: NextRequest) {
    const payload = await req.json().catch(() => ({} as any));
    const tried: Tried[] = [];

    for (const url of candidateUrls()) {
        try {
            const r = await fetch(url, {
                method: "POST",
                headers: headers(true),
                body: JSON.stringify(payload),
                cache: "no-store",
            });

            const text = await r.text().catch(() => "");
            const ct = r.headers.get("content-type");
            const attempt: Tried = {
                url,
                status: r.status,
                ok: r.ok,
                contentType: ct,
                bodyPreview: previewBody(text),
            };

            if (r.ok) {
                try {
                    const data = text ? JSON.parse(text) : {};
                    return NextResponse.json(data, {
                        status: r.status,
                        headers: { "Cache-Control": "no-store" },
                    });
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
        { error: "Save failed at integration", tried },
        { status: 502 }
    );
}
