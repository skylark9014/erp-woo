// src/app/types/sync.ts

export type PreviewItem = {
    sku: string;
    name?: string;
    has_variants?: number; // 0 or 1
    action?: 'Create' | 'Update' | 'Synced';
    fields_to_update?: string[] | 'ALL';
    regular_price?: number | null;
    stock_quantity?: number | null;
    brand?: string | null;
    categories?: string[];
    attributes?: Record<string, string>;
};

export type VariantParent = {
    sku: string;
    name?: string;
    has_variants: 1;
    action: 'Create' | 'Sync';
    attributes?: any[];
    fields_to_update?: string[] | 'ALL' | 'None';
};

export type SyncReport = {
    created: PreviewItem[];
    updated: PreviewItem[];
    skipped: any[];
    errors: any[];
    mapping: Record<string, any>;

    to_create: PreviewItem[];
    to_update: PreviewItem[];
    already_synced: PreviewItem[];

    variant_parents: VariantParent[];
    variant_to_create: PreviewItem[];
    variant_to_update: PreviewItem[];
    variant_synced: PreviewItem[];
};

export type PreviewResponse = {
    category_report?: any;
    brand_report?: any;
    attribute_report?: any;
    attribute_order?: string[];
    price_list_used?: string;
    dry_run: boolean;
    sync_report: SyncReport;
};
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
export type PreviewSyncReport = {
    to_create: any[];
    to_update: any[];
    already_synced: any[];
    variant_parents: any[];
    variant_to_create: any[];
    variant_to_update: any[];
    variant_synced: any[];
    errors: any[];
};

export type HealthResponse = {
    ok: boolean;
    erpnext: { ok: boolean;[k: string]: any };
    wordpress: { ok: boolean; status?: number | null; url: string; error?: string | null };
};

export type FullOrPartialResponse = PreviewResponse; // same envelope
