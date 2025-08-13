// src/app/lib/api.ts
import { withBase } from "@/app/lib/basePath";

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

/** Shipping JSON shapes coming from shipping_params.json */

export type ShippingSpec = {
    weight_kg?: number;
    length_cm?: number;
    width_cm?: number;
    height_cm?: number;
    shipping_class?: string;
};

export type ShippingVariablesEntry = {
    parent?: ShippingSpec;
    variations?: Record<string, ShippingSpec>;
};

export type ShippingParamsFile = {
    generated_at?: string;
    defaults?: ShippingSpec;
    simples?: Record<string, ShippingSpec>;
    variables?: Record<string, ShippingVariablesEntry>;
    meta?: any;
};

/** Envelope used by GET/POST /api/integration/shipping/params */
export type ShippingParamsDoc = {
    ok: boolean;
    path?: string;
    valid?: boolean;
    error?: string | null;
    mtime?: number;        // unix seconds
    size?: number;         // bytes
    content?: string;      // raw file text
    json?: ShippingParamsFile; // parsed object (preferred)
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
