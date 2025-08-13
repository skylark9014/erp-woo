import { NextRequest, NextResponse } from 'next/server';

function basicAuth() {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    const token = Buffer.from(`${u}:${p}`).toString('base64');
    return `Basic ${token}`;
}

export async function POST(req: NextRequest) {
    const base = process.env.INTEGRATION_BASE_URL;
    if (!base) {
        return NextResponse.json({ error: 'INTEGRATION_BASE_URL missing' }, { status: 500 });
    }

    const body = await req.json().catch(() => ({}));
    const skus: string[] = Array.isArray(body?.skus) ? body.skus : [];
    const dry_run = Boolean(body?.dry_run ?? false);

    if (!skus.length) {
        return NextResponse.json({ error: 'No SKUs provided' }, { status: 400 });
    }

    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort(), 1000 * 60 * 60); // 60 min safety

    try {
        const res = await fetch(`${base}/api/sync/partial`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': basicAuth(),
            },
            body: JSON.stringify({ skus, dry_run }),
            signal: ctrl.signal,
        });

        const data = await res.json();
        return NextResponse.json(data, { status: res.status });
    } catch (e: any) {
        return NextResponse.json({ error: e?.message || 'Partial sync failed' }, { status: 500 });
    } finally {
        clearTimeout(id);
    }
}
