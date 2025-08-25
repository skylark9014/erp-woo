"use client";
// UI section for Woo Audit Log
import React, { useEffect, useState } from "react";
import { fetchAuditLog, AuditLogEntry } from "../lib/auditLog";

export default function AuditLog() {
    const [entries, setEntries] = useState<AuditLogEntry[]>([]);
    useEffect(() => {
        fetchAuditLog().then(setEntries);
    }, []);

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Audit Log</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 font-sans">
                        <thead className="bg-gray-50">
                            <tr>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Timestamp</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Details</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {entries.length === 0 ? (
                                <tr>
                                    <td colSpan={4} className="px-6 py-4 text-sm text-gray-500 text-center">No audit log entries found.</td>
                                </tr>
                            ) : (
                                entries.map((entry: AuditLogEntry, idx: number) => (
                                    <tr key={idx}>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{entry.action}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{entry.user}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{entry.timestamp}</td>
                                        <td className="px-6 py-4 text-sm text-gray-900 font-sans">{entry.details}</td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
