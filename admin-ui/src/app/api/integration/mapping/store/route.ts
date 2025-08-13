// src/app/api/integration/mapping/store/route.ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

type Tried = {
    url: string;
    status?: number;
    ok?: boolean;
    contentType?: string | null;
    bodyPreview?: string;
    error?: string;
};

function now() {
    return new Date().toISOString();
}
function sanitizeBase(url?: string | null) {
    return (url || "http://integration:8000").replace(/\/+$/, "");
}
function buildAuthHeader(): string | undefined {
    const user = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || "";
    const pass = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || "";
    if (!user || !pass) return undefined;
    return "Basic " + Buffer.from(`${user}:${pass}`).toString("base64");
}
function buildHeaders(acceptOnly = true, withAuth = true): Headers {
    const h = new Headers();
    h.set("Accept", "application/json");
    if (!acceptOnly) h.set("Content-Type", "application/json");
    if (withAuth) {
        const t = buildAuthHeader();
        if (t) h.set("Authorization", t);
    }
    return h;
}
function bodyPreview(s: string, n = 240) {
    const clean = s.replace(/\s+/g, " ").trim();
    return clean.length <= n ? clean : clean.slice(0, n) + "…";
}
function candidateUrls(base: string): string[] {
    const b = sanitizeBase(base);
    return [
        `${b}/api/integration/mapping/store`,
        `${b}/admin/api/mapping/store`,
        `${b}/api/mapping/store`,
        `${b}/mapping/store`,
    ];
}

async function tryFetchJson(url: string, init: RequestInit) {
    try {
        const res = await fetch(url, { ...init, cache: "no-store", next: { revalidate: 0 } });
        const contentType = res.headers.get("content-type");
        const rawText = await res.clone().text().catch(() => "");
        return {
            ok: res.ok,
            status: res.status,
            contentType,
            rawText,
            error: res.ok ? undefined : `HTTP ${res.status}`,
        };
    } catch (e: any) {
        return { ok: false, status: 0, contentType: null, rawText: "", error: e?.message || String(e) };
    }
}

/* ---------------------- GET ---------------------- */
export async function GET(_req: NextRequest) {
    const base = process.env.INTEGRATION_BASE_URL || "http://integration:8000";
    const urls = candidateUrls(base);

    const tried: Tried[] = [];

    for (const url of urls) {
        const res = await tryFetchJson(url, { method: "GET", headers: buildHeaders(true, true) });

        tried.push({
            url,
            status: res.status,
            ok: res.ok,
            contentType: res.contentType,
            bodyPreview: bodyPreview(res.rawText || ""),
            error: res.error,
        });

        if (res.ok) {
            let data: any = null;
            try { data = res.rawText ? JSON.parse(res.rawText) : {}; } catch { /* no-op */ }
            if (data && typeof data === "object") {
                return NextResponse.json(data, { status: 200, headers: { "Cache-Control": "no-store" } });
            }
        }
    }

    return NextResponse.json({ error: "Not Found at integration", tried }, { status: 502 });
}

/* ---------------------- POST ---------------------- */
export async function POST(req: NextRequest) {
    const base = process.env.INTEGRATION_BASE_URL || "http://integration:8000";
    const urls = candidateUrls(base);
    const payload = await req.json().catch(() => ({}));

    const tried: Tried[] = [];

    for (const url of urls) {
        try {
            const res = await fetch(url, {
                method: "POST",
                headers: buildHeaders(false, true),
                body: JSON.stringify(payload ?? {}),
                cache: "no-store",
                next: { revalidate: 0 },
            });

            const contentType = res.headers.get("content-type");
            const rawText = await res.clone().text().catch(() => "");

            tried.push({
                url,
                status: res.status,
                ok: res.ok,
                contentType,
                bodyPreview: bodyPreview(rawText),
            });

            if (!res.ok) continue;

            let data: any = null;
            try { data = rawText ? JSON.parse(rawText) : {}; } catch { /* no-op */ }

            if (data && typeof data === "object") {
                return NextResponse.json(data, { status: 200, headers: { "Cache-Control": "no-store" } });
            }
        } catch (e: any) {
            tried.push({ url, error: e?.message || String(e) });
            continue;
        }
    }

    return NextResponse.json({ error: "Save failed at integration", tried }, { status: 502 });
}
