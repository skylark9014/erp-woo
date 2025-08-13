// src/app/api/health/route.ts
import { NextResponse } from 'next/server';

function basicAuthHeader() {
    const user = process.env.INTEGRATION_ADMIN_USER || '';
    const pass = process.env.INTEGRATION_ADMIN_PASS || '';
    if (!user || !pass) return undefined;
    const token = Buffer.from(`${user}:${pass}`).toString('base64');
    return `Basic ${token}`;
}

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL || 'http://integration:8000';
    const url = `${base}/api/health`; // <-- FastAPI health endpoint

    const headers: Record<string, string> = { Accept: 'application/json' };
    const auth = basicAuthHeader();
    if (auth) headers.Authorization = auth; // harmless if FastAPI /api/health is public

    try {
        const res = await fetch(url, { headers, cache: 'no-store' });
        const json = await res.json().catch(() => ({}));
        return NextResponse.json(json, { status: res.status });
    } catch {
        // Integration not reachable from the UI container
        return NextResponse.json(
            { ok: false, integration: { ok: false }, error: 'integration_unreachable' },
            { status: 502 }
        );
    }
}
