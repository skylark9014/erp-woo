// admin-ui/src/app/api/integration/full/route.ts
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function basicAuth(): string {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

export async function POST(req: NextRequest) {
    const base = process.env.INTEGRATION_BASE_URL!;
    let body: any = {};
    try {
        body = await req.json();
    } catch { }

    const resp = await fetch(`${base}/api/sync/full`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: basicAuth(),
        },
        body: JSON.stringify({
            dry_run: !!(body?.dryRun ?? body?.dry_run ?? false),
            purge_bin: !!(body?.purgeBin ?? body?.purge_bin ?? true),
        }),
    });
    const text = await resp.text();
    try {
        return NextResponse.json(JSON.parse(text), { status: resp.status });
    } catch {
        return new NextResponse(text, { status: resp.status, headers: { 'Content-Type': resp.headers.get('content-type') || 'text/plain' } });
    }
}
