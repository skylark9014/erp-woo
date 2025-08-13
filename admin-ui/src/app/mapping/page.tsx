'use client';

import React, { useEffect, useMemo, useState, ChangeEvent } from 'react';
import BusyOverlay from '@/app/components/BusyOverlay';
import {
    getMappingStore,
    saveMappingStore,
    type MappingStoreDoc,
    type ProductMapRow,
} from '@/app/lib/api';

type Row = Required<Pick<ProductMapRow, 'erp_item_code' | 'sku'>> &
    Omit<ProductMapRow, 'erp_item_code' | 'sku'>;

function normalizeRow(r: ProductMapRow): Row {
    return {
        erp_item_code: String(r.erp_item_code ?? ''),
        sku: String(r.sku ?? ''),
        woo_product_id: r.woo_product_id ?? undefined,
        woo_status: r.woo_status ?? '',
        brand: r.brand ?? '',
        categories: r.categories ?? '',
    };
}

export default function MappingPage() {
    const [raw, setRaw] = useState<string>('{}');
    const [json, setJson] = useState<any>({});
    const [rows, setRows] = useState<Row[]>([]);

    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    const [error, setError] = useState<string | null>(null);
    const [msg, setMsg] = useState<string | null>(null);

    // ---------- load ----------
    useEffect(() => {
        (async () => {
            setLoading(true);
            setError(null);
            setMsg(null);
            try {
                const doc: MappingStoreDoc = await getMappingStore();
                const parsed = doc?.json ?? (doc?.content ? JSON.parse(doc.content) : {});
                const effective = parsed && typeof parsed === 'object' ? parsed : {};
                const list: ProductMapRow[] = Array.isArray(effective?.products) ? effective.products : [];
                setJson(effective);
                setRows(list.map(normalizeRow));
                setRaw(JSON.stringify(effective, null, 2));
            } catch (e: any) {
                setError(e?.message || 'Failed to load mapping store.');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    // ---------- handlers ----------
    function onRawChange(e: ChangeEvent<HTMLTextAreaElement>) {
        setRaw(e.currentTarget.value);
        setMsg(null);
        setError(null);
    }

    function applyRawToState() {
        try {
            const next = JSON.parse(raw);
            const list: ProductMapRow[] = Array.isArray(next?.products) ? next.products : [];
            setJson(next);
            setRows(list.map(normalizeRow));
            setMsg('Parsed raw JSON into the table.');
        } catch {
            setError('Raw JSON is invalid. Fix it or click “Reset from table”.');
        }
    }

    function resetRawFromTable() {
        const next = { ...json, products: rows };
        setRaw(JSON.stringify(next, null, 2));
        setMsg('Raw JSON reset from the current table.');
    }

    function addRow() {
        setRows((old) => [
            {
                erp_item_code: '',
                sku: '',
                woo_product_id: undefined,
                woo_status: 'publish',
                brand: '',
                categories: '',
            },
            ...old,
        ]);
    }

    function updateRow(i: number, patch: Partial<Row>) {
        setRows((old) => {
            const copy = old.slice();
            const merged = { ...copy[i], ...patch };
            // force strings for the keys we care about
            merged.erp_item_code = String(merged.erp_item_code ?? '');
            merged.sku = String(merged.sku ?? '');
            // number coercion for product id (blank -> undefined)
            if ('woo_product_id' in patch) {
                const v = (patch as any).woo_product_id;
                merged.woo_product_id = v === '' || v === null || Number.isNaN(Number(v)) ? undefined : Number(v);
            }
            copy[i] = merged;
            return copy;
        });
    }

    function removeRow(i: number) {
        setRows((old) => old.filter((_, idx) => idx !== i));
    }

    async function onSave() {
        setSaving(true);
        setError(null);
        setMsg(null);
        try {
            // (optional) drop completely empty rows
            const cleaned = rows.filter(
                (r) => (r.erp_item_code?.trim()?.length || r.sku?.trim()?.length)
            );
            const next = { ...json, products: cleaned };
            const res = await saveMappingStore({
                data: next,
                pretty: true,
                sortKeys: true,
            });
            if (res?.ok === false) {
                throw new Error(res?.error || 'Backend reported failure saving the file.');
            }
            setJson(next);
            setRaw(JSON.stringify(next, null, 2));
            setMsg('Mapping saved.');
        } catch (e: any) {
            setError(e?.message || 'Save failed.');
        } finally {
            setSaving(false);
        }
    }

    const busy = loading || saving;
    const busyMsg = loading ? 'Loading mapping…' : saving ? 'Saving mapping…' : undefined;

    return (
        <div className="space-y-6">
            <header className="flex items-end justify-between gap-3">
                <div>
                    <h1 className="text-base font-semibold leading-7 text-gray-900">Product Mapping</h1>
                    <p className="mt-1 text-sm text-gray-600">
                        Edit <code>mapping_store.json</code> entries that map ERPNext items to Woo products.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={addRow}
                        className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                    >
                        Add Row
                    </button>
                    <button
                        onClick={onSave}
                        disabled={saving}
                        className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
                    >
                        Save
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

            {/* table */}
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <Th className="w-44">ERP Item Code</Th>
                            <Th className="w-44">SKU</Th>
                            <Th className="w-40 text-right">Woo Product ID</Th>
                            <Th className="w-40">Woo Status</Th>
                            <Th className="w-44">Brand</Th>
                            <Th>Categories</Th>
                            <Th className="w-16 text-right">—</Th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 bg-white">
                        {rows.map((r, i) => (
                            <tr key={i} className="hover:bg-gray-50">
                                <Td>
                                    <input
                                        value={r.erp_item_code}
                                        onChange={(e) => updateRow(i, { erp_item_code: e.currentTarget.value })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                                        placeholder="ERP item code"
                                    />
                                </Td>
                                <Td>
                                    <input
                                        value={r.sku}
                                        onChange={(e) => updateRow(i, { sku: e.currentTarget.value })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
                                        placeholder="SKU"
                                    />
                                </Td>
                                <Td className="text-right">
                                    <input
                                        type="number"
                                        value={r.woo_product_id ?? ''}
                                        onChange={(e) => updateRow(i, { woo_product_id: e.currentTarget.value as unknown as number })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 text-sm text-right"
                                    />
                                </Td>
                                <Td>
                                    <input
                                        value={r.woo_status ?? ''}
                                        onChange={(e) => updateRow(i, { woo_status: e.currentTarget.value })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                                        placeholder="publish / draft"
                                    />
                                </Td>
                                <Td>
                                    <input
                                        value={r.brand ?? ''}
                                        onChange={(e) => updateRow(i, { brand: e.currentTarget.value })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                                        placeholder="Brand"
                                    />
                                </Td>
                                <Td>
                                    <input
                                        value={r.categories ?? ''}
                                        onChange={(e) => updateRow(i, { categories: e.currentTarget.value })}
                                        className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                                        placeholder="Category or list"
                                    />
                                </Td>
                                <Td className="text-right">
                                    <button
                                        onClick={() => removeRow(i)}
                                        className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                                    >
                                        Remove
                                    </button>
                                </Td>
                            </tr>
                        ))}
                        {!rows.length ? (
                            <tr>
                                <td colSpan={7} className="px-4 py-10 text-center text-sm text-gray-500">
                                    No mappings yet — click “Add Row”, then Save.
                                </td>
                            </tr>
                        ) : null}
                    </tbody>
                </table>
            </div>

            {/* advanced: raw json editor */}
            <div className="rounded-lg border border-gray-200 bg-white p-3">
                <div className="mb-2 flex items-center justify-between">
                    <label className="block text-sm font-medium text-gray-700">Advanced: raw JSON</label>
                    <div className="flex gap-2">
                        <button
                            onClick={applyRawToState}
                            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                            title="Parse raw JSON into the table"
                        >
                            Apply to Table
                        </button>
                        <button
                            onClick={resetRawFromTable}
                            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
                            title="Regenerate raw JSON from the current table"
                        >
                            Reset from Table
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
                submessage="Large lists may take a moment."
            />
        </div>
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
