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
    if (!res.ok) {
        const msg = (typeof body === "object" && body && body.detail) ? body.detail : JSON.stringify(body);
        throw new Error(msg || `HTTP ${res.status}`);
    }
    return body as T;
}

// ---------- Health ----------
export async function runHealth(): Promise<HealthResponse> {
    // Proxied by Next.js: /admin/api/health → integration /api/health
    return getJson<HealthResponse>(withBase("/api/health"), { cache: "no-store" });
}

// ---------- Preview / Sync ----------
export async function runPreview() {
    // Proxied by Next.js: /admin/api/integration/preview → integration /api/sync/preview
    return getJson<any>(withBase("/api/integration/preview"), {
        method: "POST",
        cache: "no-store",
    });
}

export async function runFullSync(opts: { dryRun: boolean; purgeBin: boolean }) {
    // Proxied by Next.js: /admin/api/integration/full → integration /api/sync/full
    return getJson<any>(withBase("/api/integration/full"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: opts.dryRun, purgeBin: opts.purgeBin }),
    });
}

export async function runPartialSync(arg: { skus: string[]; dryRun: boolean }) {
    // IMPORTANT: go via UI route so it adds Basic Auth and reshapes body for the backend
    // /admin/api/integration/partial → integration /api/sync/partial
    return getJson<any>(withBase("/api/integration/partial"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skus: arg.skus, dryRun: arg.dryRun }),
    });
}

// ---------- Shipping params & sync ----------
export async function getShippingParams(): Promise<ShippingParamsDoc> {
    // Proxied by Next.js
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
},): Promise<ShippingParamsDoc> {
    const body = {
        content: payload.content ?? null,
        data: payload.data ?? null,
        pretty: payload.pretty ?? true,
        sort_keys: payload.sortKeys ?? true,
    };
    // Proxied by Next.js
    return getJson<ShippingParamsDoc>(withBase("/api/integration/shipping/params"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

export async function syncShipping(opts?: { dryRun?: boolean }): Promise<any> {
    // Proxied by Next.js
    return getJson<any>(withBase("/api/integration/shipping/sync"), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: !!opts?.dryRun }),
    });
}

// ---------- Mapping store (GET/POST) ----------
export async function getMappingStore(): Promise<MappingStoreDoc> {
    // Proxied by Next.js
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
    // Proxied by Next.js
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
