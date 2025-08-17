import type { NextApiRequest, NextApiResponse } from 'next';

function authHeader() {
    const u = process.env.INTEGRATION_ADMIN_USER ?? process.env.ADMIN_USER ?? '';
    const p = process.env.INTEGRATION_ADMIN_PASS ?? process.env.ADMIN_PASS ?? '';
    return 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== 'GET') return res.status(405).end('Method Not Allowed');

    const base = (process.env.INTEGRATION_BASE_URL || 'http://integration:8000').replace(/\/$/, '');
    const url = `${base}/admin/api/deletes/preview`;

    const upstream = await fetch(url, {
        method: 'GET',
        headers: { Authorization: authHeader() },
        cache: 'no-store',
    });

    const text = await upstream.text();
    const ct = upstream.headers.get('content-type') || 'application/json';

    res.status(upstream.status);
    res.setHeader('content-type', ct);
    if (ct.includes('application/json')) {
        try { return res.json(JSON.parse(text)); } catch { /* fall through */ }
    }
    return res.send(text);
}
