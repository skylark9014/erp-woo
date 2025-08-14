// admin-ui/src/app/api/integration/preview/route.ts
import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function basicAuth(): string {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL!;
    const resp = await fetch(`${base}/api/sync/preview`, {
        method: 'GET',
        headers: { Authorization: basicAuth() },
    });
    const text = await resp.text();
    try {
        return NextResponse.json(JSON.parse(text), { status: resp.status });
    } catch {
        return new NextResponse(text, { status: resp.status, headers: { 'Content-Type': resp.headers.get('content-type') || 'text/plain' } });
    }
}

export async function POST() {
    // allow POST as well (same as GET) to avoid caches
    return GET();
}
