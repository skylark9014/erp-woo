"use client";
import { useEffect, useState } from "react";
import { fetchInboxList, fetchInboxPayload } from "@/app/lib/inbox";
import { setInboxStatus } from "@/app/lib/inboxStatus";
import { replayInboxPayload } from "@/app/lib/inboxReplay";
import { JsonViewer } from "../components/JsonViewer";
import BusyOverlay from "../components/BusyOverlay";

export default function Inbox() {
    const [actionLoading, setActionLoading] = useState(false);
    const [rows, setRows] = useState<any[]>([]);
    // Store extracted summary fields for each row
    const [summaries, setSummaries] = useState<{ [key: string]: { orderId?: string, customer?: string, total?: number | string } }>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [expandedPayload, setExpandedPayload] = useState<any>(null);
    const [replayStatus, setReplayStatus] = useState<{ [key: string]: string }>({});
    // Use status from backend metadata
    const [archived, setArchived] = useState<{ [key: string]: boolean }>({});
    const [showArchived, setShowArchived] = useState(false);

    useEffect(() => {
        async function load() {
            setLoading(true);
            setError(null);
            try {
                const data = await fetchInboxList();
                // Filter out .status.json files
                const filtered = data.filter((row: any) => !row.name.endsWith('.status.json'));
                setRows(filtered);
                // Load archive status and summary fields from backend metadata only
                const archiveMap: { [key: string]: boolean } = {};
                const summaryMap: { [key: string]: { orderId?: string, customer?: string, total?: number | string } } = {};
                for (const row of filtered) {
                    if (row.status && (row.status.status === "archived" || row.status.status === "Archived")) {
                        archiveMap[row.name] = true;
                    } else {
                        archiveMap[row.name] = false;
                    }
                    let orderId = row.order_id ?? 'n/a';
                    let customer = row.customer ?? 'n/a';
                    let total: number | string = row.total ?? 'n/a';
                    summaryMap[row.name] = { orderId, customer, total };
                }
                setArchived(archiveMap);
                setSummaries(summaryMap);
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
            setActionLoading(false);
            return;
        }
        setActionLoading(true);
        setExpandedRow(row.name);
        try {
            const payload = await fetchInboxPayload(row.path);
            setExpandedPayload(payload);
        } catch (e) {
            setExpandedPayload({ error: "Failed to load payload" });
        } finally {
            setActionLoading(false);
        }
    }

    async function handleReplay(row: any) {
        console.log(`[INBOX] Resubmit started for file: ${row.name}`);
        setReplayStatus((prev) => ({ ...prev, [row.name]: "Replaying..." }));
        setActionLoading(true);
        try {
            const result = await replayInboxPayload(row.path);
            setReplayStatus((prev) => ({ ...prev, [row.name]: result.success ? "Replayed" : "Failed" }));
            // Refresh status labels after resubmit
            const data = await fetchInboxList();
            const filtered = data.filter((r: any) => !r.name.endsWith('.status.json'));
            setRows(filtered);
            const archiveMap: { [key: string]: boolean } = {};
            for (const r of filtered) {
                if (r.status && (r.status.status === "archived" || r.status.status === "Archived")) {
                    archiveMap[r.name] = true;
                } else {
                    archiveMap[r.name] = false;
                }
            }
            setArchived(archiveMap);
        } catch (e) {
            setReplayStatus((prev) => ({ ...prev, [row.name]: "Error" }));
        } finally {
            setActionLoading(false);
        }
        setTimeout(() => {
            setReplayStatus((prev) => ({ ...prev, [row.name]: "" }));
        }, 2000);
    }

    // Helper to determine status
    function getStatus(row: any): string {
        const status = row.status?.status;
        if (archived[row.name]) return "Archived";
        if (status === "failed") return "Failed";
        if (status === "unarchived" || status === "Unarchived") return "Unarchived";
        if (!archived[row.name] && status === "archived") return "Unarchived";
        if (replayStatus[row.name] === "Failed") return "Failed";
        if (replayStatus[row.name] === "Replayed") return "Completed";
        if (status === "completed") return "Completed";
        if (status === "pending") return "Pending";
        return "Pending";
    }

    // Archive handler
    async function handleArchive(row: any) {
        const newStatus = archived[row.name] ? "unarchived" : "archived";
        setActionLoading(true);
        await setInboxStatus(row.path, newStatus);
        // Reload inbox list to get updated status from backend
        try {
            const data = await fetchInboxList();
            const filtered = data.filter((r: any) => !r.name.endsWith('.status.json'));
            setRows(filtered);
            const archiveMap: { [key: string]: boolean } = {};
            for (const r of filtered) {
                if (r.status && (r.status.status === "archived" || r.status.status === "Archived")) {
                    archiveMap[r.name] = true;
                } else {
                    archiveMap[r.name] = false;
                }
            }
            setArchived(archiveMap);
        } catch (e) {
            // Optionally handle error
        } finally {
            setActionLoading(false);
        }
    }

    // Toggle archived
    function handleToggleArchived() {
        setShowArchived((prev) => !prev);
    }

    return (
        <div className="p-6 relative">
            <BusyOverlay show={actionLoading} title="Please wait" message="Loading payload…" submessage="This may take a moment depending on file size." />
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight text-center">Woocommerce Inbox</h1>
            <div className="mb-4 text-center">
                <button
                    className={`inline-flex items-center px-4 py-2 border text-sm font-medium rounded transition ${showArchived ? 'bg-green-600 text-white border-green-600 hover:bg-green-700' : 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700'}`}
                    onClick={handleToggleArchived}
                >
                    {showArchived ? "Hide Archived" : "Show Archived"}
                </button>
            </div>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans w-full max-w-8xl mx-auto">
                {loading && <div className="p-6 text-gray-500">Loading...</div>}
                {error && <div className="p-6 text-red-600">{error}</div>}
                {!loading && !error && (
                    <table className="w-full divide-y divide-gray-200 font-sans text-xs" style={{ tableLayout: 'fixed' }}>
                        <thead className="bg-gray-50">
                            <tr>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '15%' }}>File Name</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '8%' }}>Action</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '8%' }}>Order ID</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '12%' }}>Customer</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '8%' }}>Total</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '15%' }}>Received At</th>
                                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider" style={{ width: '8%' }}>Status</th>
                                <th scope="col" className="px-2 py-3" style={{ width: '7%' }}></th>
                                <th scope="col" className="px-2 py-3" style={{ width: '8%' }}></th>
                                <th scope="col" className="px-2 py-3" style={{ width: '8%' }}></th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {rows.length === 0 && (
                                <tr><td colSpan={6} className="px-6 py-4 text-gray-400 text-center">No payloads found.</td></tr>
                            )}
                            {rows.filter(row => showArchived || !archived[row.name]).map((row) => [
                                <tr key={row.name} className="hover:bg-blue-50 transition">
                                    <td className="px-4 py-4 font-mono text-sm text-gray-900" style={{ wordBreak: 'break-all' }}>{row.name}</td>
                                    <td className="px-4 py-4 text-sm text-gray-900 font-sans" style={{ wordBreak: 'break-all' }}>{row.webshot_action ?? '-'}</td>
                                    <td className="px-4 py-4 text-sm text-gray-900 font-sans" style={{ wordBreak: 'break-all' }}>{summaries[row.name]?.orderId || '-'}</td>
                                    <td className="px-4 py-4 text-sm text-gray-900 font-sans" style={{ wordBreak: 'break-all' }}>{summaries[row.name]?.customer || '-'}</td>
                                    <td className="px-4 py-4 text-sm text-gray-900 font-sans" style={{ wordBreak: 'break-all' }}>{summaries[row.name]?.total !== undefined ? summaries[row.name].total : '-'}</td>
                                    <td className="px-4 py-4 text-sm text-gray-900 font-sans" style={{ wordBreak: 'break-all' }}>{row.mtime ? new Date(row.mtime * 1000).toLocaleString() : "-"}</td>
                                    <td className={`px-4 py-4 font-sans ${getStatus(row) === "Completed" ? "text-green-600" : getStatus(row) === "Failed" ? "text-red-600" : getStatus(row) === "Pending" ? "text-yellow-600" : getStatus(row) === "Archived" ? "text-gray-400" : getStatus(row) === "Unarchived" ? "text-blue-600" : ""}`}>{getStatus(row)}</td>
                                    <td className="px-2 py-4 text-center">
                                        <a
                                            href="#"
                                            className={`inline-flex items-center px-2 py-1 mx-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition ${expandedRow === row.name ? 'bg-blue-50' : ''}`}
                                            onClick={e => { e.preventDefault(); handleView(row); }}
                                        >
                                            {expandedRow === row.name ? "Close" : "View"}
                                        </a>
                                    </td>
                                    <td className="px-2 py-4 text-center">
                                        <a
                                            href="#"
                                            className={`inline-flex items-center px-2 py-1 mx-1 border border-indigo-600 text-indigo-600 text-xs font-medium rounded hover:bg-indigo-50 transition ${!!replayStatus[row.name] || archived[row.name] ? 'opacity-50 pointer-events-none' : ''}`}
                                            onClick={e => { e.preventDefault(); if (!replayStatus[row.name] && !archived[row.name]) handleReplay(row); }}
                                        >
                                            {replayStatus[row.name] ? replayStatus[row.name] : "Resubmit"}
                                        </a>
                                    </td>
                                    <td className="px-2 py-4 text-center">
                                        <a
                                            href="#"
                                            className={`inline-flex items-center px-2 py-1 mx-1 border border-gray-600 text-gray-600 text-xs font-medium rounded hover:bg-gray-50 transition`}
                                            onClick={e => { e.preventDefault(); handleArchive(row); }}
                                        >
                                            {archived[row.name] ? "Unarchive" : "Archive"}
                                        </a>
                                    </td>
                                </tr>,
                                expandedRow === row.name && (
                                    <tr key={row.name + "-expanded"}>
                                        <td colSpan={6}>
                                            <div className="bg-gray-50 rounded text-xs font-mono text-gray-800 w-full">
                                                <div className="mb-2 text-xs text-gray-500">&nbsp;&nbsp;Press <span className="font-bold">ESC</span> to close</div>
                                                <div style={{ padding: '1rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', overflowX: 'auto' }}>
                                                    {expandedPayload ? <JsonViewer data={expandedPayload} /> : "Loading..."}
                                                </div>
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
    );
}
