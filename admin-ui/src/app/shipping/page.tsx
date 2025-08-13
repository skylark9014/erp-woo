'use client';

import React, { useEffect, useMemo, useState, ChangeEvent } from 'react';
import BusyOverlay from '@/app/components/BusyOverlay';
import {
    getShippingParams,
    saveShippingParams,
    syncShipping,
    type ShippingParamsDoc, // keep this one from lib/api
} from '@/app/lib/api';


/* =========================
   Types / helpers
   ========================= */

// Local type definitions (don’t import these from lib/api)
type ShippingSpec = {
    weight_kg?: number;
    length_cm?: number;
    width_cm?: number;
    height_cm?: number;
    shipping_class?: string;
};

type ShippingParamsFile = {
    generated_at?: string;
    defaults?: ShippingSpec;
    simples?: Record<string, ShippingSpec>;
    variables?: Record<
        string,
        {
            parent?: ShippingSpec;
            variations?: Record<string, ShippingSpec>;
        }
    >;
    meta?: any;
};

type VariablesEntryNorm = {
    parent: ShippingSpec;
    variations: Record<string, ShippingSpec>;
};

type NormalizedShippingParams = {
    generated_at?: string;
    defaults: ShippingSpec;
    simples: Record<string, ShippingSpec>;
    variables: Record<string, VariablesEntryNorm>;
    meta: any;
};

function deepClone<T>(x: T): T {
    return JSON.parse(JSON.stringify(x));
}

function numOrUndef(v: string): number | undefined {
    if (v === '' || v === null || v === undefined) return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
}

function defaultSpec(): ShippingSpec {
    return {
        weight_kg: undefined,
        length_cm: undefined,
        width_cm: undefined,
        height_cm: undefined,
        shipping_class: '',
    };
}

function sortBySku<T extends [string, any]>(entries: T[]): T[] {
    return [...entries].sort((a, b) => a[0].localeCompare(b[0]));
}

/** Safely pluck fields from any unknown shape, tolerating synonyms. */
function pluckDefaults(input: any): ShippingSpec {
    const def = input?.defaults ?? input?.default ?? {};
    return { ...(def || {}) };
}

function pluckSimples(input: any): Record<string, ShippingSpec> {
    // tolerate `simples`, `simple`, `simple_products`
    const s = input?.simples ?? input?.simple ?? input?.simple_products ?? {};
    return { ...(s || {}) };
}

function pluckVariables(input: any): Record<string, VariablesEntryNorm> {
    // tolerate `variables`, `variable`, `variable_products`
    const raw = input?.variables ?? input?.variable ?? input?.variable_products ?? {};
    const out: Record<string, VariablesEntryNorm> = {};
    for (const [pk, val] of Object.entries(raw as Record<string, any>)) {
        out[pk] = {
            parent: { ...(val?.parent || {}) },
            variations: { ...(val?.variations || {}) },
        };
    }
    return out;
}

/** Normalize any ShippingParamsFile into a guaranteed shape. */
function normalize(input?: ShippingParamsFile | null): NormalizedShippingParams {
    const src: any = input ?? {};
    return {
        generated_at: src.generated_at,
        defaults: pluckDefaults(src),
        simples: pluckSimples(src),
        variables: pluckVariables(src),
        meta: src.meta ?? {},
    };
}

/* =========================
   Component
   ========================= */

export default function ShippingPage() {
    const [doc, setDoc] = useState<NormalizedShippingParams | null>(null);
    const [raw, setRaw] = useState<string>('{}');

    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const [error, setError] = useState<string | null>(null);
    const [msg, setMsg] = useState<string | null>(null);

    // ---------- initial load ----------
    useEffect(() => {
        (async () => {
            setLoading(true);
            setError(null);
            setMsg(null);
            try {
                const envelope: ShippingParamsDoc = await getShippingParams();

                // Prefer parsing the raw file content if present (it’s the source of truth).
                let parsed: any = {};
                let parsedFromContent: any = null;
                if (typeof envelope?.content === 'string' && envelope.content.trim()) {
                    try {
                        parsedFromContent = JSON.parse(envelope.content);
                    } catch {
                        // If content is somehow not valid JSON, we’ll fall back to json field below.
                        parsedFromContent = null;
                    }
                }

                if (parsedFromContent && typeof parsedFromContent === 'object') {
                    parsed = parsedFromContent;
                } else if (envelope?.json && typeof envelope.json === 'object') {
                    parsed = envelope.json;
                } else {
                    parsed = {};
                }

                const normalized = normalize(parsed);
                setDoc(normalized);
                setRaw(JSON.stringify(normalized, null, 2));
            } catch (e: any) {
                setError(e?.message || 'Failed to load shipping params.');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    // ---------- derived lists ----------
    const simpleRows = useMemo(
        () => sortBySku(doc ? Object.entries(doc.simples || {}) : []),
        [doc]
    );

    const variableParents = useMemo(
        () => sortBySku(doc ? Object.entries(doc.variables || {}) : []),
        [doc]
    );

    // ---------- defaults handlers ----------
    function updateDefault(field: keyof ShippingSpec, value: string) {
        if (!doc) return;
        const next = deepClone(doc);
        (next.defaults as any)[field] =
            field === 'shipping_class' ? value : numOrUndef(value);
        setDoc(next);
        setMsg(null);
        setError(null);
    }

    // ---------- simples handlers ----------
    function addSimple() {
        if (!doc) return;
        const next = deepClone(doc);
        let base = 'NEWSKU';
        let idx = 1;
        while (next.simples[base] || next.variables[base]) {
            idx += 1;
            base = `NEWSKU${idx}`;
        }
        next.simples[base] = defaultSpec();
        setDoc(next);
    }

    function removeSimple(sku: string) {
        if (!doc) return;
        const next = deepClone(doc);
        delete next.simples[sku];
        setDoc(next);
    }

    function renameSimple(oldSku: string, newSku: string) {
        if (!doc) return;
        const trimmed = newSku.trim();
        if (!trimmed || trimmed === oldSku) return;

        const next = deepClone(doc);
        if (next.simples[trimmed] || next.variables[trimmed]) {
            setError(`SKU '${trimmed}' already exists.`);
            return;
        }
        next.simples[trimmed] = next.simples[oldSku];
        delete next.simples[oldSku];
        setDoc(next);
    }

    function updateSimple(sku: string, patch: Partial<ShippingSpec>) {
        if (!doc) return;
        const next = deepClone(doc);
        next.simples[sku] = { ...(next.simples[sku] || {}), ...patch };
        setDoc(next);
    }

    // ---------- variables handlers ----------
    function addParent() {
        if (!doc) return;
        const next = deepClone(doc);
        let base = 'NEWPARENT';
        let idx = 1;
        while (next.variables[base] || next.simples[base]) {
            idx += 1;
            base = `NEWPARENT${idx}`;
        }
        next.variables[base] = { parent: { shipping_class: '' }, variations: {} };
        setDoc(next);
    }

    function removeParent(parentSku: string) {
        if (!doc) return;
        const next = deepClone(doc);
        delete next.variables[parentSku];
        setDoc(next);
    }

    function renameParent(oldSku: string, newSku: string) {
        if (!doc) return;
        const trimmed = newSku.trim();
        if (!trimmed || trimmed === oldSku) return;

        const next = deepClone(doc);
        if (next.variables[trimmed] || next.simples[trimmed]) {
            setError(`Parent SKU '${trimmed}' already exists.`);
            return;
        }
        next.variables[trimmed] = next.variables[oldSku];
        delete next.variables[oldSku];
        setDoc(next);
    }

    function updateParentSpec(parentSku: string, patch: Partial<ShippingSpec>) {
        if (!doc) return;
        const next = deepClone(doc);
        const entry: VariablesEntryNorm =
            next.variables[parentSku] || { parent: {}, variations: {} };
        entry.parent = { ...(entry.parent || {}), ...patch };
        next.variables[parentSku] = entry;
        setDoc(next);
    }

    function addVariation(parentSku: string) {
        if (!doc) return;
        const next = deepClone(doc);
        const entry: VariablesEntryNorm =
            next.variables[parentSku] || { parent: {}, variations: {} };

        let base = `${parentSku}-VAR`;
        let idx = 1;
        while (entry.variations[base] || next.simples[base]) {
            idx += 1;
            base = `${parentSku}-VAR${idx}`;
        }
        entry.variations[base] = defaultSpec();
        next.variables[parentSku] = entry;
        setDoc(next);
    }

    function removeVariation(parentSku: string, sku: string) {
        if (!doc) return;
        const next = deepClone(doc);
        const entry = next.variables[parentSku];
        delete entry.variations[sku];
        setDoc(next);
    }

    function renameVariation(parentSku: string, oldSku: string, newSku: string) {
        if (!doc) return;
        const trimmed = newSku.trim();
        if (!trimmed || trimmed === oldSku) return;

        const next = deepClone(doc);
        const entry = next.variables[parentSku];
        if (entry.variations[trimmed] || next.simples[trimmed] || next.variables[trimmed]) {
            setError(`Variation SKU '${trimmed}' already exists.`);
            return;
        }
        entry.variations[trimmed] = entry.variations[oldSku];
        delete entry.variations[oldSku];
        setDoc(next);
    }

    function updateVariation(parentSku: string, sku: string, patch: Partial<ShippingSpec>) {
        if (!doc) return;
        const next = deepClone(doc);
        const entry = next.variables[parentSku];
        entry.variations[sku] = { ...(entry.variations[sku] || {}), ...patch };
        setDoc(next);
    }

    // ---------- raw JSON helpers ----------
    function onRawChange(e: ChangeEvent<HTMLTextAreaElement>) {
        setRaw(e.currentTarget.value);
        setMsg(null);
        setError(null);
    }

    function applyRawToState() {
        try {
            const parsed = JSON.parse(raw);
            const normalized = normalize(parsed);
            setDoc(normalized);
            setMsg('Parsed raw JSON into the editor.');
        } catch {
            setError('Raw JSON is invalid. Fix it or click “Reset from editor”.');
        }
    }

    function resetRawFromState() {
        if (!doc) return;
        setRaw(JSON.stringify(doc, null, 2));
        setMsg('Raw JSON reset from the current editor.');
    }

    // ---------- actions ----------
    async function onSave() {
        if (!doc) return;
        setSaving(true);
        setError(null);
        setMsg(null);
        try {
            const res = await saveShippingParams({ data: doc, pretty: true, sortKeys: true });
            if (res?.ok === false) throw new Error(res?.error || 'Backend reported failure saving the file.');

            // prefer the returned content/json if present; otherwise keep what we have
            let nextDoc = doc;
            try {
                const parsed =
                    (res?.content && JSON.parse(res.content)) ||
                    (res?.json as any) ||
                    {};
                nextDoc = normalize(parsed);
            } catch {
                // ignore parsing errors, retain current doc
            }

            setDoc(nextDoc);
            setRaw(JSON.stringify(nextDoc, null, 2));
            setMsg('Shipping parameters saved.');
        } catch (e: any) {
            setError(e?.message || 'Save failed.');
        } finally {
            setSaving(false);
        }
    }

    async function onSync() {
        setSyncing(true);
        setError(null);
        setMsg(null);
        try {
            const res = await syncShipping({ dryRun: false });
            if (res?.ok === false) throw new Error(res?.error || 'Sync failed.');
            setMsg('Shipping sync started. Check logs for progress.');
        } catch (e: any) {
            setError(e?.message || 'Shipping sync failed.');
        } finally {
            setSyncing(false);
        }
    }

    const busy = loading || saving || syncing;
    const busyMsg =
        loading ? 'Loading shipping parameters…'
            : saving ? 'Saving shipping parameters…'
                : syncing ? 'Syncing shipping rules to WooCommerce…'
                    : undefined;

    /* =========================
       Render
       ========================= */

    return (
        <div className="space-y-6">
            <header className="flex items-end justify-between gap-3">
                <div>
                    <h1 className="text-base font-semibold leading-7 text-gray-900">Shipping</h1>
                    <p className="mt-1 text-sm text-gray-600">
                        Edit <code>shipping_params.json</code> (<code>defaults</code>, <code>simples</code>, <code>variables</code>) and sync to WooCommerce.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={onSave}
                        disabled={saving || !doc}
                        className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
                    >
                        Save
                    </button>
                    <button
                        onClick={onSync}
                        disabled={syncing}
                        className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-emerald-500 disabled:opacity-50"
                        title="Run shipping sync"
                    >
                        Sync Shipping
                    </button>
                </div>
            </header>

            {/* alerts */}
            {error && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>
            )}
            {msg && (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{msg}</div>
            )}

            {/* defaults editor */}
            <section className="rounded-lg border border-gray-200 bg-white p-4">
                <h2 className="mb-3 text-sm font-semibold text-gray-900">Defaults</h2>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-5">
                    <FieldNumber label="Weight (kg)" value={doc?.defaults?.weight_kg} onChange={(v) => updateDefault('weight_kg', v)} />
                    <FieldNumber label="Length (cm)" value={doc?.defaults?.length_cm} onChange={(v) => updateDefault('length_cm', v)} />
                    <FieldNumber label="Width (cm)" value={doc?.defaults?.width_cm} onChange={(v) => updateDefault('width_cm', v)} />
                    <FieldNumber label="Height (cm)" value={doc?.defaults?.height_cm} onChange={(v) => updateDefault('height_cm', v)} />
                    <FieldText label="Shipping Class" value={doc?.defaults?.shipping_class ?? ''} onChange={(v) => updateDefault('shipping_class', v)} />
                </div>
            </section>

            {/* simples */}
            <section className="space-y-2">
                <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-gray-900">Simple products</h2>
                    <button
                        onClick={addSimple}
                        className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                    >
                        Add Simple
                    </button>
                </div>

                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <Th className="w-56">SKU</Th>
                                <Th className="w-28 text-right">Weight (kg)</Th>
                                <Th className="w-28 text-right">Length (cm)</Th>
                                <Th className="w-28 text-right">Width (cm)</Th>
                                <Th className="w-28 text-right">Height (cm)</Th>
                                <Th className="w-44">Class</Th>
                                <Th className="w-20 text-right">—</Th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 bg-white">
                            {simpleRows.map(([sku, spec]) => (
                                <tr key={sku} className="hover:bg-gray-50">
                                    <Td>
                                        <input
                                            defaultValue={sku}
                                            onBlur={(e) => {
                                                const nextSku = e.currentTarget.value.trim();
                                                if (nextSku && nextSku !== sku) renameSimple(sku, nextSku);
                                            }}
                                            className="w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                                            placeholder="SKU123"
                                        />
                                    </Td>
                                    <Td className="text-right">
                                        <input
                                            defaultValue={spec?.weight_kg ?? ''}
                                            onBlur={(e) => updateSimple(sku, { weight_kg: numOrUndef(e.currentTarget.value) })}
                                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                            inputMode="decimal"
                                        />
                                    </Td>
                                    <Td className="text-right">
                                        <input
                                            defaultValue={spec?.length_cm ?? ''}
                                            onBlur={(e) => updateSimple(sku, { length_cm: numOrUndef(e.currentTarget.value) })}
                                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                            inputMode="decimal"
                                        />
                                    </Td>
                                    <Td className="text-right">
                                        <input
                                            defaultValue={spec?.width_cm ?? ''}
                                            onBlur={(e) => updateSimple(sku, { width_cm: numOrUndef(e.currentTarget.value) })}
                                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                            inputMode="decimal"
                                        />
                                    </Td>
                                    <Td className="text-right">
                                        <input
                                            defaultValue={spec?.height_cm ?? ''}
                                            onBlur={(e) => updateSimple(sku, { height_cm: numOrUndef(e.currentTarget.value) })}
                                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                            inputMode="decimal"
                                        />
                                    </Td>
                                    <Td>
                                        <input
                                            defaultValue={spec?.shipping_class ?? ''}
                                            onBlur={(e) => updateSimple(sku, { shipping_class: e.currentTarget.value })}
                                            className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                                        />
                                    </Td>
                                    <Td className="text-right">
                                        <button
                                            onClick={() => removeSimple(sku)}
                                            className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                        >
                                            Remove
                                        </button>
                                    </Td>
                                </tr>
                            ))}
                            {!simpleRows.length ? (
                                <tr>
                                    <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">
                                        No simple products — click “Add Simple”.
                                    </td>
                                </tr>
                            ) : null}
                        </tbody>
                    </table>
                </div>
            </section>

            {/* variables (parents + variations) */}
            <section className="space-y-2">
                <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-gray-900">Variable products</h2>
                    <button
                        onClick={addParent}
                        className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                    >
                        Add Variable Parent
                    </button>
                </div>

                {variableParents.map(([parentSku, entry]) => {
                    const variations = sortBySku(Object.entries(entry.variations || {}));
                    return (
                        <div key={parentSku} className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                            {/* Slightly darker header bar for contrast */}
                            <div className="flex items-center justify-between border-b border-gray-200 bg-gray-100 px-4 py-2">
                                <div className="flex flex-wrap items-center gap-3">
                                    <span className="text-xs font-semibold uppercase text-gray-700">Parent</span>
                                    <input
                                        defaultValue={parentSku}
                                        onBlur={(e) => {
                                            const nextSku = e.currentTarget.value.trim();
                                            if (nextSku && nextSku !== parentSku) renameParent(parentSku, nextSku);
                                        }}
                                        className="rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                                    />
                                    <span className="text-xs text-gray-700">Class:</span>
                                    <input
                                        defaultValue={entry.parent?.shipping_class ?? ''}
                                        onBlur={(e) => updateParentSpec(parentSku, { shipping_class: e.currentTarget.value })}
                                        className="w-44 rounded border border-gray-300 px-2 py-1 text-sm"
                                        placeholder="optional"
                                    />
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => addVariation(parentSku)}
                                        className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                    >
                                        Add Variation
                                    </button>
                                    <button
                                        onClick={() => removeParent(parentSku)}
                                        className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                    >
                                        Remove Parent
                                    </button>
                                </div>
                            </div>

                            <table className="min-w-full divide-y divide-gray-200">
                                <thead className="bg-white">
                                    <tr>
                                        <Th className="w-56">Variation SKU</Th>
                                        <Th className="w-28 text-right">Weight (kg)</Th>
                                        <Th className="w-28 text-right">Length (cm)</Th>
                                        <Th className="w-28 text-right">Width (cm)</Th>
                                        <Th className="w-28 text-right">Height (cm)</Th>
                                        <Th className="w-44">Class</Th>
                                        <Th className="w-20 text-right">—</Th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100 bg-white">
                                    {variations.map(([sku, spec]) => (
                                        <tr key={sku} className="hover:bg-gray-50">
                                            <Td>
                                                <input
                                                    defaultValue={sku}
                                                    onBlur={(e) => {
                                                        const nextSku = e.currentTarget.value.trim();
                                                        if (nextSku && nextSku !== sku) renameVariation(parentSku, sku, nextSku);
                                                    }}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                                                    placeholder={`${parentSku}-VARIANT`}
                                                />
                                            </Td>
                                            <Td className="text-right">
                                                <input
                                                    defaultValue={spec?.weight_kg ?? ''}
                                                    onBlur={(e) => updateVariation(parentSku, sku, { weight_kg: numOrUndef(e.currentTarget.value) })}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                                    inputMode="decimal"
                                                />
                                            </Td>
                                            <Td className="text-right">
                                                <input
                                                    defaultValue={spec?.length_cm ?? ''}
                                                    onBlur={(e) => updateVariation(parentSku, sku, { length_cm: numOrUndef(e.currentTarget.value) })}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                                    inputMode="decimal"
                                                />
                                            </Td>
                                            <Td className="text-right">
                                                <input
                                                    defaultValue={spec?.width_cm ?? ''}
                                                    onBlur={(e) => updateVariation(parentSku, sku, { width_cm: numOrUndef(e.currentTarget.value) })}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                                    inputMode="decimal"
                                                />
                                            </Td>
                                            <Td className="text-right">
                                                <input
                                                    defaultValue={spec?.height_cm ?? ''}
                                                    onBlur={(e) => updateVariation(parentSku, sku, { height_cm: numOrUndef(e.currentTarget.value) })}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                                    inputMode="decimal"
                                                />
                                            </Td>
                                            <Td>
                                                <input
                                                    defaultValue={spec?.shipping_class ?? ''}
                                                    onBlur={(e) => updateVariation(parentSku, sku, { shipping_class: e.currentTarget.value })}
                                                    className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                                                />
                                            </Td>
                                            <Td className="text-right">
                                                <button
                                                    onClick={() => removeVariation(parentSku, sku)}
                                                    className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                                >
                                                    Remove
                                                </button>
                                            </Td>
                                        </tr>
                                    ))}
                                    {!variations.length ? (
                                        <tr>
                                            <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">
                                                No variations yet — click “Add Variation”.
                                            </td>
                                        </tr>
                                    ) : null}
                                </tbody>
                            </table>
                        </div>
                    );
                })}

                {!variableParents.length ? (
                    <div className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-500">
                        No variable parents — click “Add Variable Parent”.
                    </div>
                ) : null}
            </section>

            {/* advanced: raw json editor */}
            <div className="rounded-lg border border-gray-200 bg-white p-3">
                <div className="mb-2 flex items-center justify-between">
                    <label className="block text-sm font-medium text-gray-700">Advanced: raw JSON</label>
                    <div className="flex gap-2">
                        <button
                            onClick={applyRawToState}
                            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                            title="Parse raw JSON into the editor"
                        >
                            Apply to Editor
                        </button>
                        <button
                            onClick={resetRawFromState}
                            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                            title="Regenerate raw JSON from the current editor"
                        >
                            Reset from Editor
                        </button>
                    </div>
                </div>
                <textarea
                    className="block h-[320px] w-full resize-none rounded-md border border-gray-300 bg-gray-50 p-3 font-mono text-sm text-gray-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    spellCheck={false}
                    value={raw}
                    onChange={onRawChange}
                />
            </div>

            <BusyOverlay
                show={busy}
                title="Please wait"
                message={busyMsg}
                submessage="Large rule sets may take a moment."
            />
        </div>
    );
}

/* =========================
   Small presentational helpers
   ========================= */

function FieldNumber({
    label,
    value,
    onChange,
}: {
    label: string;
    value?: number;
    onChange: (v: string) => void;
}) {
    return (
        <label className="block">
            <div className="text-xs text-gray-600">{label}</div>
            <input
                defaultValue={value ?? ''}
                onBlur={(e) => onChange(e.currentTarget.value)}
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                inputMode="decimal"
            />
        </label>
    );
}

function FieldText({
    label,
    value,
    onChange,
}: {
    label: string;
    value: string;
    onChange: (v: string) => void;
}) {
    return (
        <label className="block">
            <div className="text-xs text-gray-600">{label}</div>
            <input
                defaultValue={value}
                onBlur={(e) => onChange(e.currentTarget.value)}
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
            />
        </label>
    );
}

function Th({ children, className = '' }: { children: React.ReactNode; className?: string }) {
    return (
        <th scope="col" className={`px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 ${className}`}>
            {children}
        </th>
    );
}
function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
    return <td className={`px-4 py-3 align-middle ${className}`}>{children}</td>;
}
