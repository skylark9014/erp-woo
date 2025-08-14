import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function basicAuth(): string {
    const u = process.env.INTEGRATION_ADMIN_USER || '';
    const p = process.env.INTEGRATION_ADMIN_PASS || '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

function extractJobId(res: Response, bodyText: string | null): string | null {
    let jobId =
        res.headers.get('x-job-id') ||
        res.headers.get('x-jobid') ||
        res.headers.get('job-id') ||
        null;

    if (!jobId) {
        const loc = res.headers.get('location') || '';
        if (loc) {
            const parts = loc.split('/');
            jobId = parts[parts.length - 1] || null;
        }
    }
    if (!jobId && bodyText) {
        try {
            const j = JSON.parse(bodyText);
            jobId = j?.job_id || j?.id || null;
        } catch {
            const m = bodyText.match(/[A-Za-z0-9_-]{10,}/);
            if (m) jobId = m[0];
        }
    }
    return jobId;
}

export async function POST(req: NextRequest) {
    const base = process.env.INTEGRATION_BASE_URL!;
    let body: any = {};
    try { body = await req.json(); } catch { /* empty */ }

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
        cache: 'no-store',
    });

    const text = await resp.text();
    const ct = resp.headers.get('content-type') || '';

    if (resp.status === 202) {
        const jobId = extractJobId(resp, text);
        if (!jobId) {
            return NextResponse.json({ error: '202 from backend but no job id found' }, { status: 502 });
        }
        return NextResponse.json({ job_id: jobId }, { status: 202 });
    }

    if (ct.includes('json')) {
        try { return NextResponse.json(JSON.parse(text), { status: resp.status }); } catch { /* fallthrough */ }
    }
    return new NextResponse(text, {
        status: resp.status,
        headers: { 'Content-Type': ct || 'text/plain' },
    });
}
