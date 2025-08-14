// admin-ui/src/app/api/health/route.ts
import { NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL!;
    const resp = await fetch(`${base}/api/health`, { cache: 'no-store' });
    const text = await resp.text();
    try {
        return NextResponse.json(JSON.parse(text), { status: resp.status });
    } catch {
        return new NextResponse(text, { status: resp.status, headers: { 'Content-Type': resp.headers.get('content-type') || 'text/plain' } });
    }
}
