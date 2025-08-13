// src/app/lib/api.ts

import type { PreviewResponse } from '@/app/types/sync';

const basePath = (process.env.NEXT_PUBLIC_ADMIN_BASE_PATH || '').replace(/\/$/, ''); // e.g. "/admin" or ""
const withBase = (p: string) => `${basePath}${p.startsWith('/') ? p : `/${p}`}`;

export type HealthService = {
    ok: boolean;
    status?: number;
    rest_status?: number; // WP may report REST status separately
    url?: string;
    error?: string;
};

export type HealthResponse = {
    ok: boolean;
    integration?: { ok: boolean; error?: string };
    erpnext?: HealthService;
    woocommerce?: HealthService; // backend uses "woocommerce"
};

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
    const res = await fetch(url, init);
    const ct = res.headers.get('content-type') || '';

    if (!ct.includes('application/json')) {
        const text = await res.text().catch(() => '');
        throw new Error(
            `Non-JSON response (${res.status}) from ${url}${text ? `: ${text.slice(0, 180)}` : ''}`
        );
    }

    const body = (await res.json()) as T & { _httpStatus?: number };
    (body as any)._httpStatus = res.status;
    return body as T;
}

/** Health: UI proxy -> FastAPI /api/health (exposed as /admin/api/health) */
export async function runHealth(): Promise<HealthResponse> {
    const url = withBase('/api/health');
    return getJson<HealthResponse>(url, { cache: 'no-store' });
}

/** Preview (dry run) */
export async function runPreview(): Promise<PreviewResponse> {
    const url = withBase('/api/sync/preview');
    return getJson<PreviewResponse>(url, { method: 'GET', cache: 'no-store' });
}

/** Full sync */
export async function runFullSync(opts: { dryRun: boolean; purgeBin: boolean }): Promise<PreviewResponse> {
    const url = withBase('/api/sync/full');
    return getJson<PreviewResponse>(url, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: opts.dryRun, purge_bin: opts.purgeBin }),
    });
}

/** Partial (selected SKUs) */
export async function runPartialSync(opts: { skus: string[]; dryRun: boolean }): Promise<PreviewResponse> {
    const url = withBase('/api/sync/partial');
    return getJson<PreviewResponse>(url, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skus: opts.skus, dry_run: opts.dryRun }),
    });
}
