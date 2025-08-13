import { NextResponse } from 'next/server';

function basicAuth() {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    const token = Buffer.from(`${u}:${p}`).toString('base64');
    return `Basic ${token}`;
}

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL;
    if (!base) {
        return NextResponse.json({ error: 'INTEGRATION_BASE_URL missing' }, { status: 500 });
    }

    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort(), 1000 * 60 * 15); // 15 min timeout, previews can be heavy

    try {
        // Your FastAPI exposes GET/POST at /api/sync/preview
        const res = await fetch(`${base}/api/sync/preview`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': basicAuth(),
            },
            body: JSON.stringify({ dry_run: true }),
            signal: ctrl.signal,
            // keepalive: true  // optional
        });

        const data = await res.json();
        return NextResponse.json(data, { status: res.status });
    } catch (e: any) {
        return NextResponse.json({ error: e?.message || 'Preview failed' }, { status: 500 });
    } finally {
        clearTimeout(id);
    }
}
