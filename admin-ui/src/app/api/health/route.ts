export const runtime = "nodejs";
export const dynamic = "force-dynamic";

import { NextResponse } from "next/server";

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL;
    const user = process.env.INTEGRATION_ADMIN_USER || "";
    const pass = process.env.INTEGRATION_ADMIN_PASS || "";

    if (!base) {
        return NextResponse.json({ ok: false, error: "INTEGRATION_BASE_URL not set" }, { status: 500 });
    }

    const headers: Record<string, string> = {};
    if (user || pass) {
        const token = Buffer.from(`${user}:${pass}`).toString("base64");
        headers["Authorization"] = `Basic ${token}`;
    }

    try {
        const r = await fetch(`${base}/api/health`, { headers, cache: "no-store" });
        const text = await r.text();
        try {
            const json = JSON.parse(text);
            return NextResponse.json(json, { status: r.status });
        } catch {
            return NextResponse.json({ ok: false, error: `Non-JSON from integration: HTTP ${r.status}` }, { status: 502 });
        }
    } catch (e: any) {
        return NextResponse.json({ ok: false, error: e?.message || "Fetch failed" }, { status: 502 });
    }
}
