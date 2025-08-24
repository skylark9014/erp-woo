"use client";
import { useEffect, useState } from "react";
import { runHealth } from "@/app/lib/api";
import { fetchRecentWebhookEvents } from "@/app/lib/webhook";
import { fetchWebhookPayload } from "@/app/lib/webhookPayload";

export default function WebhookStatus() {
    const [health, setHealth] = useState<any>(null);
    const [events, setEvents] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [expandedPayload, setExpandedPayload] = useState<any>(null);

    useEffect(() => {
        async function load() {
            setLoading(true);
            setError(null);
            try {
                const h = await runHealth();
                setHealth(h);
                const ev = await fetchRecentWebhookEvents();
                setEvents(ev);
            } catch (e: any) {
                setError(e?.message || "Failed to load data");
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

    async function handleView(ev: any) {
        if (expandedRow === ev.name) {
            setExpandedRow(null);
            setExpandedPayload(null);
            return;
        }
        setExpandedRow(ev.name);
        setExpandedPayload(null);
        try {
            const payload = await fetchWebhookPayload(ev.path);
            setExpandedPayload(payload);
        } catch (e) {
            setExpandedPayload({ error: "Failed to load payload" });
        }
    }

    return (
        <div className="p-6 max-w-3xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Webhook Status & Recent Activity</h1>
            {/* Health Card */}
            <div className="mb-8">
                <div className="bg-white shadow-lg rounded-xl p-6 flex flex-col gap-2 border border-blue-100 font-sans">
                    <div className="flex items-center gap-2">
                        <span className={`inline-block w-3 h-3 rounded-full ${health?.ok ? 'bg-green-500' : 'bg-red-500'}`}></span>
                        <span className="font-semibold text-lg text-gray-900 font-sans">{health?.ok ? 'Connected' : 'Disconnected'}</span>
                    </div>
                    <div className="text-gray-600 font-sans">Last Received: <span className="font-mono text-gray-900">{events[0]?.ts ? new Date(events[0].ts).toLocaleString() : "--"}</span></div>
                    <div className="text-gray-600 font-sans">Last Error: <span className="font-mono text-red-600">{health?.integration?.error || "None"}</span></div>
                </div>
            </div>
            {/* Loading/Error */}
            {loading && <div className="text-gray-500 animate-pulse">Loading...</div>}
            {error && <div className="text-red-600 font-semibold">{error}</div>}
            {/* Recent Events Table */}
            {!loading && !error && (
                <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                    <h2 className="text-xl font-bold px-6 pt-6 pb-2 text-gray-900 leading-tight font-sans">Recent Webhook Events</h2>
                    <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200 font-sans">
                            <thead className="bg-gray-50">
                                <tr>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Event Type</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Resource</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Received At</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Size</th>
                                    <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {events.length === 0 && (
                                    <tr><td colSpan={5} className="px-6 py-4 text-gray-400 text-center">No events found.</td></tr>
                                )}
                                {events.map((ev) => [
                                    <tr key={ev.name} className="hover:bg-blue-50 transition">
                                        <td className="px-6 py-4 font-mono text-sm text-gray-900">{ev.topic || ev.name}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{ev.kind || "-"}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{ev.ts ? new Date(ev.ts).toLocaleString() : "-"}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{ev.size ? `${ev.size} bytes` : "-"}</td>
                                        <td className="px-6 py-4">
                                            <button className={`inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition ${expandedRow === ev.name ? 'bg-blue-50' : ''}`} onClick={() => handleView(ev)}>{expandedRow === ev.name ? "Close" : "View"}</button>
                                        </td>
                                    </tr>,
                                    expandedRow === ev.name && (
                                        <tr key={ev.name + "-expanded"}>
                                            <td colSpan={5}>
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
                    </div>
                </div>
            )}
        </div>
    );
}
