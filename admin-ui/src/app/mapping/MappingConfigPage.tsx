"use client";
import React, { useEffect, useState } from 'react';
import BusyOverlay from '@/app/components/BusyOverlay';
import {
    getMappingStore,
    saveMappingStore,
    getFieldMappings,
    setFieldMappings,
    getTransformationRules,
    setTransformationRules,
    type MappingStoreDoc,
    type ProductMapRow,
} from '@/app/lib/api';

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
    return (
        <button
            className={`px-4 py-2 text-sm font-semibold rounded-t-md border-b-2 ${active ? 'border-indigo-600 bg-white' : 'border-transparent bg-gray-50 text-gray-500'}`}
            onClick={onClick}
        >
            {children}
        </button>
    );
}

export default function MappingConfigPage() {
    const [tab, setTab] = useState<'fields' | 'rules'>('fields');
    // Field Mapping
    const [fieldMap, setFieldMap] = useState<Record<string, string>>({});
    const [fieldLoading, setFieldLoading] = useState(false);
    // Transformation Rules (table UI)
    const [rules, setRules] = useState<any[]>([]);
    const [rulesLoading, setRulesLoading] = useState(false);
    const [ruleRows, setRuleRows] = useState<any[]>([
        {
            source: 'woo_status',
            target: 'erp_status',
            type: 'map',
            mapping: 'processing=In Progress, completed=Done'
        },
        {
            source: 'woo_sku',
            target: 'erp_sku',
            type: 'regex',
            mapping: '^SKU-(.*)$ → $1'
        },
        {
            source: 'woo_total',
            target: 'erp_amount',
            type: 'copy',
            mapping: ''
        }
    ]);
    // Always show initial JSON preview for example rules
    const initialExampleRules = [
        { source: 'woo_status', target: 'erp_status', type: 'map', mapping: 'processing=In Progress, completed=Done' },
        { source: 'woo_sku', target: 'erp_sku', type: 'regex', mapping: '^SKU-(.*)$ → $1' },
        { source: 'woo_total', target: 'erp_amount', type: 'copy', mapping: '' }
    ];
    const [jsonPreview, setJsonPreview] = useState<string>(JSON.stringify(initialExampleRules.map(row => {
        if (!row.source || !row.target) return null;
        if (row.type === 'map') {
            const mappingObj: Record<string, string> = {};
            (row.mapping || '').split(',').forEach((pair: string) => {
                const [k, v] = pair.split('=').map((s: string) => s.trim());
                if (k && v) mappingObj[k] = v;
            });
            return { source: row.source, target: row.target, type: 'map', mapping: mappingObj };
        } else if (row.type === 'regex') {
            const [pattern, replace] = (row.mapping || '').split('→').map((s: string) => s.trim());
            return { source: row.source, target: row.target, type: 'regex', pattern, replace };
        } else {
            return { source: row.source, target: row.target, type: 'copy' };
        }
    }).filter(Boolean), null, 2));
    // Alerts
    const [msg, setMsg] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Loaders
    useEffect(() => {
        if (tab === 'fields') {
            setFieldLoading(true);
            getFieldMappings().then(res => setFieldMap(res.fields || {})).catch(e => setError('Failed to load field mapping')).finally(() => setFieldLoading(false));
        } else if (tab === 'rules') {
            setRulesLoading(true);
            getTransformationRules().then(res => {
                const exampleRows = [
                    { source: 'woo_status', target: 'erp_status', type: 'map', mapping: 'processing=In Progress, completed=Done' },
                    { source: 'woo_sku', target: 'erp_sku', type: 'regex', mapping: '^SKU-(.*)$ → $1' },
                    { source: 'woo_total', target: 'erp_amount', type: 'copy', mapping: '' }
                ];
                const rulesArr = Array.isArray(res.rules) ? res.rules : [];
                setRules(rulesArr);
                setRuleRows(rulesArr.length ? rulesArr : exampleRows);
            }).catch(e => {
                // On error, show example rules
                setRuleRows([
                    { source: 'woo_status', target: 'erp_status', type: 'map', mapping: 'processing=In Progress, completed=Done' },
                    { source: 'woo_sku', target: 'erp_sku', type: 'regex', mapping: '^SKU-(.*)$ → $1' },
                    { source: 'woo_total', target: 'erp_amount', type: 'copy', mapping: '' }
                ]);
                setError('Failed to load rules');
            }).finally(() => setRulesLoading(false));
        }
    }, [tab]);
    async function saveFields() {
        setFieldLoading(true);
        setError(null);
        setMsg(null);
        try {
            const res = await setFieldMappings(fieldMap);
            if (res?.ok === false) throw new Error(res?.error || 'Backend error');
            setMsg('Field mapping saved.');
        } catch (e: any) {
            setError(e?.message || 'Save failed.');
        } finally {
            setFieldLoading(false);
        }
    }
    async function saveRules() {
        setRulesLoading(true);
        setError(null);
        setMsg(null);
        try {
            // Parse ruleRows into canonical JSON
            const parsed = ruleRows.map(row => {
                if (!row.source || !row.target) return null;
                if (row.type === 'map') {
                    // Parse mapping string: key1=val1, key2=val2
                    const mappingObj: Record<string, string> = {};
                    ((row.mapping || '') as string).split(',').forEach((pair: string) => {
                        const [k, v] = pair.split('=').map((s: string) => s.trim());
                        if (k && v) {
                            (mappingObj as Record<string, string>)[k] = v;
                        }
                    });
                    return { source: row.source, target: row.target, type: 'map', mapping: mappingObj };
                } else if (row.type === 'regex') {
                    // Parse pattern and replace
                    const [pattern, replace] = (row.mapping || '').split('→').map((s: string) => s.trim());
                    return { source: row.source, target: row.target, type: 'regex', pattern, replace };
                } else {
                    // Copy type
                    return { source: row.source, target: row.target, type: 'copy' };
                }
            }).filter(Boolean);
            setJsonPreview(JSON.stringify(parsed, null, 2));
            const res = await setTransformationRules(parsed);
            if (res?.ok === false) throw new Error(res?.error || 'Backend error');
            setMsg('Transformation rules saved.');
        } catch (e: any) {
            setError(e?.message || 'Save failed.');
        } finally {
            setRulesLoading(false);
        }
    }
    // UI
    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-base font-semibold leading-7 text-gray-900">Mapping Configuration</h1>
                <p className="mt-1 text-sm text-gray-600">Configure Woo ↔ ERPNext field mapping and transformation rules.</p>
            </header>
            <nav className="flex gap-2 border-b mb-4">
                <TabButton active={tab === 'fields'} onClick={() => setTab('fields')}>Field Mapping</TabButton>
                <TabButton active={tab === 'rules'} onClick={() => setTab('rules')}>Transformation Rules</TabButton>
            </nav>
            {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>}
            {msg && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{msg}</div>}
            {/* Field Mapping Tab */}
            {tab === 'fields' && (
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white mb-4">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">ERPNext Field</th>
                                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Woo Field</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-100">
                            {Object.entries(fieldMap).map(([erp, woo], i) => (
                                <tr key={erp} className="hover:bg-gray-50">
                                    <td className="px-4 py-3 font-mono text-xs align-middle">{erp}</td>
                                    <td className="px-4 py-3 align-middle">
                                        <input value={woo} onChange={e => setFieldMap(fm => ({ ...fm, [erp]: e.target.value }))} className="w-full rounded border border-gray-300 px-2 py-1 text-xs" />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <button onClick={saveFields} className="mt-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50">Save Field Mapping</button>
                </div>
            )}
            {/* Transformation Rules Tab (Table UI) */}
            {tab === 'rules' && (
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white mb-4">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Source Field</th>
                                <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Target Field</th>
                                <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Type</th>
                                <th className="px-2 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Mapping/Pattern</th>
                                <th className="px-2 py-2"></th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-100">
                            {ruleRows.map((row, i) => (
                                <tr key={i}>
                                    <td className="px-2 py-2"><input value={row.source} onChange={e => setRuleRows(rr => rr.map((r, idx) => idx === i ? { ...r, source: e.target.value } : r))} className="w-full rounded border border-gray-300 px-2 py-1 text-xs" /></td>
                                    <td className="px-2 py-2"><input value={row.target} onChange={e => setRuleRows(rr => rr.map((r, idx) => idx === i ? { ...r, target: e.target.value } : r))} className="w-full rounded border border-gray-300 px-2 py-1 text-xs" /></td>
                                    <td className="px-2 py-2">
                                        <select value={row.type} onChange={e => setRuleRows(rr => rr.map((r, idx) => idx === i ? { ...r, type: e.target.value } : r))} className="w-full rounded border border-gray-300 px-2 py-1 text-xs">
                                            <option value="copy">copy</option>
                                            <option value="map">map</option>
                                            <option value="regex">regex</option>
                                        </select>
                                    </td>
                                    <td className="px-2 py-2">
                                        <input value={row.mapping || ''} onChange={e => setRuleRows(rr => rr.map((r, idx) => idx === i ? { ...r, mapping: e.target.value } : r))} className="w-full rounded border border-gray-300 px-2 py-1 text-xs" placeholder={row.type === 'map' ? 'key1=val1, key2=val2' : row.type === 'regex' ? 'pattern → replace' : ''} />
                                    </td>
                                    <td className="px-2 py-2">
                                        <button onClick={() => setRuleRows(rr => rr.filter((_, idx) => idx !== i))} className="text-xs text-red-500">Delete</button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <div className="flex gap-2 mt-2">
                        <button onClick={() => setRuleRows(rr => [...rr, { source: '', target: '', type: 'copy', mapping: '' }])} className="rounded-md bg-gray-200 px-3 py-2 text-sm font-semibold text-gray-700 shadow-sm hover:bg-gray-300">Add Rule</button>
                        <button onClick={saveRules} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50">Save Rules</button>
                    </div>
                    <div className="mt-4">
                        <label className="block text-xs font-semibold text-gray-500 mb-1">JSON Preview:</label>
                        <pre className="bg-gray-50 border border-gray-200 rounded p-2 text-xs font-mono whitespace-pre-wrap">{jsonPreview}</pre>
                    </div>
                    <div className="mt-4">
                        <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
                            <strong>How to use Transformation Rules:</strong>
                            <ul className="list-disc ml-4 mt-2">
                                <li>Each row defines a rule for transforming WooCommerce data before syncing to ERPNext.</li>
                                <li><b>Source Field</b>: The field name from WooCommerce.</li>
                                <li><b>Target Field</b>: The field name in ERPNext.</li>
                                <li><b>Type</b>:
                                    <ul className="list-disc ml-4">
                                        <li><b>copy</b>: Directly copy the value from source to target.</li>
                                        <li><b>map</b>: Map specific values. Enter as <code>key1=val1, key2=val2</code> (e.g., <code>processing=In Progress, completed=Done</code>).</li>
                                        <li><b>regex</b>: Transform using a pattern. Enter as <code>pattern → replace</code> (e.g., <code>^SKU-(.*)$ → $1</code>).</li>
                                    </ul>
                                </li>
                                <li>Click <b>Add Rule</b> to add more rows. Use <b>Delete</b> to remove a rule.</li>
                                <li>The <b>JSON Preview</b> shows the rules as they will be saved and used by the backend.</li>
                                <li>Click <b>Save Rules</b> to apply your changes.</li>
                            </ul>
                        </div>
                    </div>
                </div>
            )}
            <BusyOverlay show={fieldLoading || rulesLoading} title="Please wait" message="Loading or saving…" />
        </div>
    );
}
