// Server-only helper for calling the FastAPI middleware with Basic auth.
import { headers } from 'next/headers';

const BASE = process.env.INTEGRATION_BASE_URL || '';
const USER = process.env.ADMIN_USER || '';
const PASS = process.env.ADMIN_PASS || '';

function assertEnv() {
    const miss: string[] = [];
    if (!BASE) miss.push('INTEGRATION_BASE_URL');
    if (!USER) miss.push('ADMIN_USER');
    if (!PASS) miss.push('ADMIN_PASS');
    if (miss.length) {
        throw new Error(
            `Missing required environment variables: ${miss.join(', ')}. ` +
            `Set them in your root .env.`
        );
    }
}

function basic() {
    return 'Basic ' + Buffer.from(`${USER}:${PASS}`).toString('base64');
}

export async function callMiddleware(path: string, init?: RequestInit) {
    assertEnv();

    const res = await fetch(`${BASE}${path}`, {
        method: 'GET',
        // never send cookies; we only use token/basic auth server-to-server
        credentials: 'omit',
        cache: 'no-store',
        next: { revalidate: 0 },
        ...init,
        headers: {
            'Authorization': basic(),
            ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
            ...(init?.headers || {}),
        },
    });

    const text = await res.text();
    try {
        return { res, data: JSON.parse(text) };
    } catch {
        return { res, data: { raw: text } };
    }
}
