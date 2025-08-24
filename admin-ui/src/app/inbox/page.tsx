"use client";
import { useEffect, useState } from "react";
import { fetchInboxList, fetchInboxPayload } from "@/app/lib/inbox";
import { replayInboxPayload } from "@/app/lib/inboxReplay";

export default function Inbox() {
    const [rows, setRows] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [expandedPayload, setExpandedPayload] = useState<any>(null);
    const [replayStatus, setReplayStatus] = useState<{ [key: string]: string }>({});

    useEffect(() => {
        async function load() {
            setLoading(true);
            setError(null);
            try {
                const data = await fetchInboxList();
                setRows(data);
            } catch (e: any) {
                setError(e?.message || "Failed to load inbox");
            } finally {
                setLoading(false);
            }
        }
        load();
        // ESC key handler to close expanded view
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
        if (expandedRow === row.name) {
            setExpandedRow(null);
            setExpandedPayload(null);
            return;
        }
        setExpandedRow(row.name);
        setExpandedPayload(null);
        try {
            const payload = await fetchInboxPayload(row.path);
            setExpandedPayload(payload);
        } catch (e) {
            setExpandedPayload({ error: "Failed to load payload" });
        }
    }

    async function handleReplay(row: any) {
        setReplayStatus((prev) => ({ ...prev, [row.name]: "Replaying..." }));
        try {
            const result = await replayInboxPayload(row.path);
            setReplayStatus((prev) => ({ ...prev, [row.name]: result.ok ? "Replayed" : "Failed" }));
        } catch (e) {
            setReplayStatus((prev) => ({ ...prev, [row.name]: "Error" }));
        }
        setTimeout(() => {
            setReplayStatus((prev) => ({ ...prev, [row.name]: "" }));
        }, 2000);
    }

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Webhook Inbox (Archived Payloads)</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    {loading && <div className="p-6 text-gray-500">Loading...</div>}
                    {error && <div className="p-6 text-red-600">{error}</div>}
                    {!loading && !error && (
                        <table className="min-w-full divide-y divide-gray-200 font-sans" style={{ tableLayout: 'fixed' }}>
                            <thead className="bg-gray-50">
                                <tr>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-2/6" style={{ width: '24%' }}>File Name</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Event Type</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Received At</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '16%' }}>Status</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '14%' }}>View</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/6" style={{ width: '14%' }}>Replay</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {rows.length === 0 && (
                                    <tr><td colSpan={6} className="px-6 py-4 text-gray-400 text-center">No archived payloads found.</td></tr>
                                )}
                                {rows.map((row) => [
                                    <tr key={row.name} className="hover:bg-blue-50 transition">
                                        <td className="px-6 py-4 font-mono text-sm text-gray-900">{row.name}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{row.topic || "-"}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{row.mtime ? new Date(row.mtime * 1000).toLocaleString() : "-"}</td>
                                        <td className={`px-6 py-4 font-sans ${row.name.includes("created") ? "text-green-600" : "text-yellow-600"}`}>{row.name.includes("created") ? "Archived" : "Pending"}</td>
                                        <td className="px-6 py-4">
                                            <button className={`inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition ${expandedRow === row.name ? 'bg-blue-50' : ''}`} onClick={() => handleView(row)}>{expandedRow === row.name ? "Close" : "View"}</button>
                                        </td>
                                        <td className="px-6 py-4">
                                            <button className="inline-flex items-center px-3 py-1 border border-indigo-600 text-indigo-600 text-xs font-medium rounded hover:bg-indigo-50 transition" onClick={() => handleReplay(row)} disabled={!!replayStatus[row.name]}>
                                                {replayStatus[row.name] ? replayStatus[row.name] : "Replay"}
                                            </button>
                                        </td>
                                    </tr>,
                                    expandedRow === row.name && (
                                        <tr key={row.name + "-expanded"}>
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
