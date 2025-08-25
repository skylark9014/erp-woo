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
    // Transformation Rules
    const [rules, setRules] = useState<Record<string, any>>({});
    const [rulesLoading, setRulesLoading] = useState(false);
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
            getTransformationRules().then(res => setRules(res.rules || {})).catch(e => setError('Failed to load rules')).finally(() => setRulesLoading(false));
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
            const res = await setTransformationRules(rules);
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
            {/* Transformation Rules Tab */}
            {tab === 'rules' && (
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white mb-4">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Transformation Rules (JSON)</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-100">
                            <tr>
                                <td className="px-4 py-3 align-middle">
                                    <textarea value={JSON.stringify(rules, null, 2)} onChange={e => setRules(JSON.parse(e.target.value))} className="block w-full h-64 rounded border border-gray-300 p-2 font-mono text-xs" />
                                </td>
                            </tr>
                        </tbody>
                    </table>
                    <button onClick={saveRules} className="mt-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50">Save Rules</button>
                </div>
            )}
            <BusyOverlay show={fieldLoading || rulesLoading} title="Please wait" message="Loading or saving…" />
        </div>
    );
}
