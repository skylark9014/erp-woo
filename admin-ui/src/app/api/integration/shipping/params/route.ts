// src/app/api/integration/shipping/params/route.ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BASE = (process.env.INTEGRATION_BASE_URL || "http://integration:8000").replace(/\/+$/, "");
const USER = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || "";
const PASS = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || "";

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

/* ---------------------- GET ---------------------- */
export async function GET() {
    const url = `${BASE}/api/integration/shipping/params`;
    try {
        const r = await fetch(url, { headers: headers(false), cache: "no-store" });
        const text = await r.text();
        try {
            const data = JSON.parse(text);
            return NextResponse.json(data, { status: r.status, headers: { "Cache-Control": "no-store" } });
        } catch {
            return NextResponse.json({ error: `Non-JSON from integration (${r.status})`, url }, { status: 502 });
        }
    } catch (e: any) {
        return NextResponse.json({ error: e?.message || "Fetch failed", url }, { status: 502 });
    }
}

/* ---------------------- POST ---------------------- */
export async function POST(req: NextRequest) {
    const url = `${BASE}/api/integration/shipping/params`;
    let payload: any = {};
    try {
        payload = await req.json();
    } catch {
        payload = {};
    }
    try {
        const r = await fetch(url, {
            method: "POST",
            headers: headers(true),
            body: JSON.stringify(payload),
            cache: "no-store",
        });
        const text = await r.text();
        try {
            const data = JSON.parse(text);
            return NextResponse.json(data, { status: r.status, headers: { "Cache-Control": "no-store" } });
        } catch {
            return NextResponse.json({ error: `Non-JSON from integration (${r.status})`, url }, { status: 502 });
        }
    } catch (e: any) {
        return NextResponse.json({ error: e?.message || "Fetch failed", url }, { status: 502 });
    }
}
