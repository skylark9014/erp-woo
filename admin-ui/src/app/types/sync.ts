// src/app/types/sync.ts

// ---------- Items shown in preview tables ----------
export type PreviewItem = {
    sku: string;
    name?: string;
    has_variants?: number; // 0 or 1
    action?: "Create" | "Update" | "Synced";
    fields_to_update?: string[] | "ALL";
    regular_price?: number | null;
    stock_quantity?: number | null;
    brand?: string | null;
    categories?: string[];
    attributes?: Record<string, string>;
    // (Some preview rows may also include helper fields like parent_sku, attr_abbr, etc.)
};

export type VariantParent = {
    sku: string;
    name?: string;
    has_variants: 1;
    action: "Create" | "Sync";
    attributes?: any[];
    fields_to_update?: string[] | "ALL" | "None";
};

// ---------- Delete preview ----------
export interface WooRef {
    id?: number;
    status?: string;
    parent_id?: number;
}

export interface DeleteCandidate {
    sku: string;
    name?: string;
    reason?: string;
    woo?: WooRef;
    parent_sku?: string; // for variant rows (not used in simple table)
}

// ---------- Unified SyncReport (single source of truth) ----------
export interface SyncReport {
    // mutation results (when not dry_run)
    created: PreviewItem[];
    updated: PreviewItem[];
    skipped: any[];
    errors: any[];
    mapping: Record<string, any>;

    // preview buckets
    to_create: PreviewItem[];
    to_update: PreviewItem[];
    already_synced: PreviewItem[];

    // variant buckets
    variant_parents: VariantParent[];
    variant_to_create: PreviewItem[];
    variant_to_update: PreviewItem[];
    variant_synced: PreviewItem[];

    // NEW: delete previews
    to_delete?: DeleteCandidate[];
    variant_to_delete?: DeleteCandidate[];
    variant_parents_to_delete?: DeleteCandidate[];

    // optional metadata (present in preview responses)
    meta?: {
        generated_at?: string;
        dry_run?: boolean;
        counts?: {
            to_create?: number;
            to_update?: number;
            to_delete?: number;
            variant_to_create?: number;
            variant_to_update?: number;
            variant_to_delete?: number;
            variant_parents?: number;
            variant_parents_to_delete?: number;
            already_synced?: number;
            variant_synced?: number;
            errors?: number;
        };
    };
}

// ---------- Envelope types ----------
export type PreviewResponse = {
    category_report?: any;
    brand_report?: any;
    attribute_report?: any;
    attribute_order?: string[];
    price_list_used?: string;
    dry_run: boolean;
    sync_report: SyncReport;
};

export type FullOrPartialResponse = PreviewResponse; // same envelope

// Useful in a few places; keep as-is
export type PreviewSyncReport = Pick<
    SyncReport,
    | "to_create"
    | "to_update"
    | "already_synced"
    | "variant_parents"
    | "variant_to_create"
    | "variant_to_update"
    | "variant_synced"
    | "errors"
>;

// ---------- UI helpers ----------
export type Counts = {
    toCreate: number;
    toUpdate: number;
    synced: number;
    vToCreate: number;
    vToUpdate: number;
    vSynced: number;
    parents: number;
    errors: number;
};

// ---------- Health & Jobs ----------
export type HealthResponse = {
    ok: boolean;

    // used by page.tsx
    integration?: { ok: boolean;[k: string]: any };
    erpnext?: { ok: boolean; status?: number | null;[k: string]: any };
    woocommerce?: { ok: boolean; status?: number | null; rest_status?: number | null;[k: string]: any };

    // sometimes present from the admin health endpoint
    wordpress?: { ok: boolean; status?: number | null; url?: string; error?: string | null;[k: string]: any };
};

export type SyncJobStatus = "queued" | "running" | "done" | "error";
export type SyncJob = {
    id?: string;
    job_id?: string;
    status: SyncJobStatus;
    started?: number | null;
    finished?: number | null;
    request?: { dry_run?: boolean; purge_bin?: boolean };
    error?: string;
    result?: any; // same envelope as PreviewResponse on success
};
