import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function basicAuth(): string {
    // Try integration creds first, fall back to ADMIN_* if that's what you set
    const u = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

export async function GET(
    _req: NextRequest,
    ctx: { params: Promise<{ jobId: string }> } // <-- params is a Promise
) {
    const { jobId } = await ctx.params;         // <-- await it
    const base = process.env.INTEGRATION_BASE_URL || 'http://integration:8000';
    const url = `${base}/api/sync/status/${encodeURIComponent(jobId)}`;

    const resp = await fetch(url, {
        method: 'GET',
        headers: {
            Authorization: basicAuth(),
            Accept: 'application/json',
        },
        cache: 'no-store',
        next: { revalidate: 0 },
    });

    const text = await resp.text();
    try {
        return NextResponse.json(JSON.parse(text), { status: resp.status });
    } catch {
        return new NextResponse(text, {
            status: resp.status,
            headers: { 'Content-Type': resp.headers.get('content-type') || 'text/plain' },

        });
    }
}
