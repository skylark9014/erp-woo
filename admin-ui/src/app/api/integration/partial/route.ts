// admin-ui/src/app/api/integration/partial/route.ts
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function basicAuth(): string {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

async function forward(body: any) {
    const base = process.env.INTEGRATION_BASE_URL!;
    const resp = await fetch(`${base}/api/sync/partial`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: basicAuth(),
        },
        body: JSON.stringify({
            skus: body?.skus ?? [],
            dry_run: !!(body?.dryRun ?? body?.dry_run ?? false),
        }),
    });
    const text = await resp.text();
    // pass through status & body (JSON if possible)
    try {
        return NextResponse.json(JSON.parse(text), { status: resp.status });
    } catch {
        return new NextResponse(text, { status: resp.status, headers: { 'Content-Type': resp.headers.get('content-type') || 'text/plain' } });
    }
}

export async function POST(req: NextRequest) {
    try {
        const body = await req.json();
        return await forward(body);
    } catch (e: any) {
        return NextResponse.json({ error: e?.message || 'Bad Request' }, { status: 400 });
    }
}
