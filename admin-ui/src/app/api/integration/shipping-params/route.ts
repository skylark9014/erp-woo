import { NextRequest, NextResponse } from 'next/server';

function basicAuthHeader() {
    const user = process.env.INTEGRATION_ADMIN_USER || '';
    const pass = process.env.INTEGRATION_ADMIN_PASS || '';
    if (!user || !pass) return undefined;
    const token = Buffer.from(`${user}:${pass}`).toString('base64');
    return `Basic ${token}`;
}

const base = process.env.INTEGRATION_BASE_URL || 'http://integration:8000';
const target = `${base}/admin/api/config/shipping-params`;

export async function GET() {
    const headers: Record<string, string> = { Accept: 'application/json' };
    const auth = basicAuthHeader();
    if (auth) headers['Authorization'] = auth;

    const res = await fetch(target, { headers, cache: 'no-store' });
    const json = await res.json().catch(() => ({}));
    return NextResponse.json(json, { status: res.status });
}

export async function PUT(req: NextRequest) {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        Accept: 'application/json',
    };
    const auth = basicAuthHeader();
    if (auth) headers['Authorization'] = auth;

    const body = await req.text(); // pass-through
    const res = await fetch(target, {
        method: 'PUT',
        headers,
        body,
        cache: 'no-store',
    });
    const json = await res.json().catch(() => ({}));
    return NextResponse.json(json, { status: res.status });
}
