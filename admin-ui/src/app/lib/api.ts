function buildAuthHeader(): string | undefined {
    const user = process.env.INTEGRATION_ADMIN_USER || process.env.ADMIN_USER || "";
    const pass = process.env.INTEGRATION_ADMIN_PASS || process.env.ADMIN_PASS || "";
    if (!user || !pass) return undefined;
    return "Basic " + Buffer.from(`${user}:${pass}`).toString("base64");
}
// src/app/lib/api.ts
import { withBase } from "@/app/lib/basePath";
import type { PreviewResponse } from "../types/sync";

const PREVIEW_KEY = "sync_preview_snapshot";

/** Individual service health */
export type HealthService = {
    ok: boolean;
    status?: number;
    rest_status?: number;
    url?: string;
    error?: string;
};

/** /api/health response */
export type HealthResponse = {
    ok: boolean;
    integration?: { ok: boolean; error?: string };
    erpnext?: HealthService;
    woocommerce?: HealthService;
    /** internal helper field from getJson; not from the backend */
    _httpStatus?: number;
};

/** Shipping params doc (used by GET and POST) */
export type ShippingParamsDoc = {
    ok: boolean;
    path?: string;
    valid?: boolean;
    error?: string | null;
    mtime?: number;
    size?: number;
    content?: string;
    json?: any;
    saved?: boolean;
};

/** Mapping store types */
export type ProductMapRow = {
    erp_item_code?: string;
    sku?: string;
    woo_product_id?: number;
    woo_status?: string;
    brand?: string;
    categories?: string;
};

export type MappingStoreDoc = {
    ok: boolean;
    path?: string;
    valid?: boolean;
    error?: string | null;
    mtime?: number;
    size?: number;
    content?: string;
    json?: { products?: ProductMapRow[] } | any;
    saved?: boolean;
};

// ---------- Generic JSON fetch ----------
async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
    const res = await fetch(url, init);
    let body: any = null;
    try {
        body = await res.json();
    } catch {
        throw new Error(`Non-JSON response (${res.status}) from ${url}`);
    }
    (body as any)._httpStatus = res.status;
    if (!res.ok) {
        const msg = (typeof body === "object" && body && body.detail) ? body.detail : JSON.stringify(body);
        throw new Error(msg || `HTTP ${res.status}`);
    }
    return body as T;
}

// ---------- Health ----------
export async function runHealth(): Promise<HealthResponse> {
    return getJson<HealthResponse>(withBase("/api/health"), { cache: "no-store" });
}

// ---------- Preview ----------
export async function runPreview() {
    return getJson<any>("/api/integration/preview", {
        method: "POST",
        cache: "no-store",
    });
}

/* ------------------------------------------------------------------ */
/* Async full sync (202 + polling) API                                */
/* ------------------------------------------------------------------ */
export type SyncJob = {
    id: string;
    status: 'queued' | 'running' | 'done' | 'error';
    result?: any;
    error?: string | any;
    progress?: number;
    message?: string;
};

export type StartFullSyncResponse =
    | { kind: 'sync'; result: any }
    | { kind: 'async'; job_id: string };

function extractJobIdFromResponse(res: Response, text: string | null): string | null {
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

    if (!jobId && text) {
        try {
            const j = JSON.parse(text);
            jobId = j?.job_id || j?.id || null;
        } catch {
            const m = text.match(/[A-Za-z0-9_-]{10,}/);
            if (m) jobId = m[0];
        }
    }
    return jobId;
}

export async function startFullSyncAsync(opts: { dryRun: boolean; purgeBin: boolean }): Promise<StartFullSyncResponse> {
    const res = await fetch("/api/integration/full", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: opts.dryRun, purgeBin: opts.purgeBin }),
    });

    // 202 — async job
    if (res.status === 202) {
        const text = await res.text();
        const jobId = extractJobIdFromResponse(res, text);
        if (!jobId) throw new Error("202 Accepted but no job id was provided.");
        return { kind: 'async', job_id: jobId };
    }

    // synchronous path
    const text = await res.text();
    try {
        const body = JSON.parse(text);
        (body as any)._httpStatus = res.status;
        if (!res.ok) {
            const msg = (typeof body === "object" && body && body.detail) ? body.detail : JSON.stringify(body);
            throw new Error(msg || `HTTP ${res.status}`);
        }
        return { kind: 'sync', result: body };
    } catch {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return { kind: 'sync', result: { raw: text } };
    }
}

export async function getFullSyncStatus(jobId: string): Promise<SyncJob> {
    const res = await fetch(`/api/integration/status/${encodeURIComponent(jobId)}`, {
        method: 'GET',
        cache: 'no-store',
    });
    const text = await res.text();
    if (!res.ok) {
        throw new Error(`Status ${res.status}: ${text}`);
    }
    try {
        const js = JSON.parse(text);
        const status = (js?.status || '').toLowerCase();
        return {
            id: js?.id || jobId,
            status: (['queued', 'running', 'done', 'error'].includes(status) ? status : 'running') as SyncJob['status'],
            result: js?.result,
            error: js?.error || js?.detail,
            progress: typeof js?.progress === 'number' ? js.progress : undefined,
            message: js?.message,
        };
    } catch {
        throw new Error(`Bad status payload: ${text}`);
    }
}

/**
 * Backward-compatible helper:
 * If some code still calls runFullSync(), handle 202 by polling until completion
 * so those callers *block* until the job finishes.
 */
export async function runFullSync(opts: { dryRun: boolean; purgeBin: boolean }) {
    const start = await startFullSyncAsync(opts);
    if (start.kind === 'sync') return start.result;

    let delay = 800;
    while (true) {
        const s = await getFullSyncStatus(start.job_id);
        if (s.status === 'done') return s.result;
        if (s.status === 'error') {
            const message = (typeof s.error === 'string' ? s.error : JSON.stringify(s.error || {}));
            throw new Error(message || 'Full sync failed');
        }
        await new Promise(r => setTimeout(r, delay));
        delay = Math.min(delay + 400, 4000);
    }
}

// ---------- Partial sync ----------
export async function runPartialSync(arg: { skus: string[]; dryRun: boolean }) {
    return getJson<any>("/api/integration/partial", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skus: arg.skus, dryRun: arg.dryRun }),
    });
}

// ---------- Shipping params & sync ----------
export async function getShippingParams(): Promise<ShippingParamsDoc> {
    const headers: Record<string, string> = {};
    const auth = buildAuthHeader();
    if (auth) headers["Authorization"] = auth;
    return getJson<ShippingParamsDoc>(withBase("/api/integration/shipping/params"), {
        method: "GET",
        cache: "no-store",
        headers,
    });
}

export async function saveShippingParams(payload: {
    content?: string;
    data?: any;
    pretty?: boolean;
    sortKeys?: boolean;
},): Promise<ShippingParamsDoc> {
    const body = {
        content: payload.content ?? null,
        data: payload.data ?? null,
        pretty: payload.pretty ?? true,
        sort_keys: payload.sortKeys ?? true,
    };
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const auth = buildAuthHeader();
    if (auth) headers["Authorization"] = auth;
    return getJson<ShippingParamsDoc>(withBase("/api/integration/shipping/params"), {
        method: "POST",
        cache: "no-store",
        headers,
        body: JSON.stringify(body),
    });
}

export async function syncShipping(opts?: { dryRun?: boolean }): Promise<any> {
    return getJson<any>("/api/integration/shipping/sync", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: !!opts?.dryRun }),
    });
}

// ---------- Mapping store ----------
export async function getMappingStore(): Promise<MappingStoreDoc> {
    return getJson<MappingStoreDoc>("/api/integration/mapping/store", {
        method: "GET",
        cache: "no-store",
    });
}

export async function saveMappingStore(payload: {
    content?: string;
    data?: any;
    pretty?: boolean;
    sortKeys?: boolean;
}): Promise<MappingStoreDoc> {
    const body = {
        content: payload.content ?? null,
        data: payload.data ?? null,
        pretty: payload.pretty ?? true,
        sort_keys: payload.sortKeys ?? true,
    };
    return getJson<MappingStoreDoc>("/api/integration/mapping/store", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

// --- Mapping & Configuration API helpers ---
export async function getFieldMappings(): Promise<{ ok: boolean; fields: Record<string, string> }> {
    return getJson("/api/integration/mapping/fields", { cache: 'no-store' });
}
export async function setFieldMappings(fields: Record<string, string>): Promise<any> {
    return getJson("/api/integration/mapping/fields", {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(fields),
    });
}
export async function getTransformationRules(): Promise<{ ok: boolean; rules: Record<string, any> }> {
    return getJson("/api/integration/mapping/rules", { cache: 'no-store' });
}
export async function setTransformationRules(rules: Record<string, any>): Promise<any> {
    return getJson("/api/integration/mapping/rules", {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(rules),
    });
}

// -------------- preview cache (unchanged) -------------
export function loadCachedPreview(): PreviewResponse | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = localStorage.getItem(PREVIEW_KEY);
        return raw ? (JSON.parse(raw) as PreviewResponse) : null;
    } catch {
        return null;
    }
}

export function saveCachedPreview(p: PreviewResponse | null) {
    if (typeof window === "undefined") return;
    try {
        if (p) localStorage.setItem(PREVIEW_KEY, JSON.stringify(p));
        else localStorage.removeItem(PREVIEW_KEY);
    } catch { }
}

export function clearCachedPreview() {
    if (typeof window === "undefined") return;
    try {
        localStorage.removeItem(PREVIEW_KEY);
    } catch { }
}

// ---- Delete preview/run ----
// keep existing type
export type DeleteRunResponse = {
    ok: boolean;
    results: Array<{ id: number; status_code: number; ok: boolean; error?: string }>;
    force?: boolean;
};

export async function loadDeleteCandidates() {
    return getJson("/api/deletes/preview", { cache: 'no-store' });
}

// canonical (singular) name
export async function runDelete(payload: { ids: number[]; force?: boolean; purgeBin?: boolean }): Promise<DeleteRunResponse> {
    return getJson<DeleteRunResponse>("/api/deletes/run", {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

// optional alias, if you still call runDeletes() elsewhere
export const runDeletes = runDelete;