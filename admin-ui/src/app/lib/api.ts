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
    mtime?: number;        // unix seconds
    size?: number;         // bytes
    content?: string;      // raw file text
    json?: any;            // parsed object
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
    return body as T;
}

// ---------- Health ----------
export async function runHealth(): Promise<HealthResponse> {
    return getJson<HealthResponse>(withBase("/api/health"), { cache: "no-store" });
}

// ---------- Preview / Sync ----------
export async function runPreview() {
    return getJson<any>(withBase("/api/sync/preview"), { method: "GET", cache: "no-store" });
}

export async function runFullSync(opts: { dryRun: boolean; purgeBin: boolean }) {
    return getJson<any>(withBase("/api/sync/full"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: opts.dryRun, purge_bin: opts.purgeBin }),
    });
}

export async function runPartialSync(opts: { skus: string[]; dryRun: boolean }) {
    return getJson<any>(withBase("/api/sync/partial"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skus: opts.skus, dry_run: opts.dryRun }),
    });
}

// ---------- Shipping params & sync ----------
export async function getShippingParams(): Promise<ShippingParamsDoc> {
    return getJson<ShippingParamsDoc>(withBase("/api/integration/shipping/params"), {
        method: "GET",
        cache: "no-store",
    });
}

export async function saveShippingParams(payload: {
    content?: string;
    data?: any;
    pretty?: boolean;
    sortKeys?: boolean;
}): Promise<ShippingParamsDoc> {
    const body = {
        content: payload.content ?? null,
        data: payload.data ?? null,
        pretty: payload.pretty ?? true,
        sort_keys: payload.sortKeys ?? true,
    };
    return getJson<ShippingParamsDoc>(withBase("/api/integration/shipping/params"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

export async function syncShipping(opts?: { dryRun?: boolean }): Promise<any> {
    return getJson<any>(withBase("/api/integration/shipping/sync"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: !!opts?.dryRun }),
    });
}

// ---------- Mapping store (GET/POST) ----------
export async function getMappingStore(): Promise<MappingStoreDoc> {
    return getJson<MappingStoreDoc>(withBase("/api/integration/mapping/store"), {
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
    return getJson<MappingStoreDoc>(withBase("/api/integration/mapping/store"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

// -------------- hydrate the Synchronise page from the saved snapshot -------------

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