"use client";
import { useEffect, useState } from "react";
import { fetchJobsList, fetchJobPayload } from "@/app/lib/jobs";
import { retryJob } from "@/app/lib/jobRetry";

// UI section for Woo Job Queue Status
export default function Jobs() {
    const [rows, setRows] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [expandedPayload, setExpandedPayload] = useState<any>(null);
    const [retryStatus, setRetryStatus] = useState<{ [key: string]: string }>({});

    useEffect(() => {
        async function load() {
            setLoading(true);
            setError(null);
            try {
                const data = await fetchJobsList();
                setRows(data);
            } catch (e: any) {
                setError(e?.message || "Failed to load jobs");
            } finally {
                setLoading(false);
            }
        }
        load();
        function handleEsc(e: KeyboardEvent) {
            if (e.key === "Escape") {
                setExpandedRow(null);
                setExpandedPayload(null);
            }
        }
        window.addEventListener("keydown", handleEsc);
        return () => window.removeEventListener("keydown", handleEsc);
    }, []);

    async function handleView(row: any) {
        if (expandedRow === row.id) {
            setExpandedRow(null);
            setExpandedPayload(null);
            return;
        }
        setExpandedRow(row.id);
        setExpandedPayload(null);
        try {
            const payload = await fetchJobPayload(row.id);
            setExpandedPayload(payload);
        } catch (e) {
            setExpandedPayload({ error: "Failed to load job payload" });
        }
    }

    async function handleRetry(row: any) {
        setRetryStatus((prev) => ({ ...prev, [row.id]: "Retrying..." }));
        try {
            const result = await retryJob(row.id);
            setRetryStatus((prev) => ({ ...prev, [row.id]: result.ok ? "Retried" : result.message || "Failed" }));
        } catch (e) {
            setRetryStatus((prev) => ({ ...prev, [row.id]: "Error" }));
        }
        setTimeout(() => {
            setRetryStatus((prev) => ({ ...prev, [row.id]: "" }));
        }, 2000);
    }

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Job Queue Status</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    {loading && <div className="p-6 text-gray-500">Loading...</div>}
                    {error && <div className="p-6 text-red-600">{error}</div>}
                    {!loading && !error && (
                        <table className="min-w-full divide-y divide-gray-200 font-sans" style={{ tableLayout: 'fixed' }}>
                            <thead className="bg-gray-50">
                                <tr>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-2/6" style={{ width: '24%' }}>Job Type</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Resource</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Event</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Status</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '14%' }}>Created At</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '14%' }}>Actions</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {rows.length === 0 && (
                                    <tr><td colSpan={6} className="px-6 py-4 text-gray-400 text-center">No jobs found.</td></tr>
                                )}
                                {rows.map((row) => [
                                    <tr key={row.id} className="hover:bg-blue-50 transition">
                                        <td className="px-6 py-4 font-mono text-sm text-gray-900">{row.kind}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{row.resource || '-'}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{row.event || '-'}</td>
                                        <td className={`px-6 py-4 font-sans ${row.status === 'done' ? 'text-green-600' : 'text-yellow-600'}`}>{row.status}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{row.created_at ? new Date(row.created_at).toLocaleString() : '-'}</td>
                                        <td className="px-6 py-4">
                                            <button className="inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition" onClick={() => handleRetry(row)} disabled={!!retryStatus[row.id]}>
                                                {retryStatus[row.id] ? retryStatus[row.id] : "Retry"}
                                            </button>
                                            <button className={`inline-flex items-center px-3 py-1 border border-gray-600 text-gray-600 text-xs font-medium rounded hover:bg-gray-50 transition ml-2 ${expandedRow === row.id ? 'bg-gray-100' : ''}`} onClick={() => handleView(row)}>{expandedRow === row.id ? "Close" : "View"}</button>
                                        </td>
                                    </tr>,
                                    expandedRow === row.id && (
                                        <tr key={row.id + "-expanded"}>
                                            <td colSpan={6}>
                                                <div className="bg-gray-50 rounded text-xs font-mono text-gray-800 w-full">
                                                    <div className="mb-2 text-xs text-gray-500">Press <span className="font-bold">ESC</span> to close</div>
                                                    <pre style={{ margin: 0, width: '100%', boxSizing: 'border-box', whiteSpace: 'pre-wrap', wordBreak: 'break-word', padding: '1rem' }}>
                                                        {expandedPayload ? (typeof expandedPayload === "object" ? JSON.stringify(expandedPayload, null, 2) : String(expandedPayload)) : "Loading..."}
                                                    </pre>
                                                </div>
                                            </td>
                                        </tr>
                                    )
                                ])}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>
    );
}
