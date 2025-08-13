'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

export default function ClientButtons() {
    const router = useRouter();
    const [loading, setLoading] = useState(false);

    async function callApi(path: string, body?: unknown) {
        const res = await fetch(path, {
            method: 'POST',
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(`Request failed ${res.status}: ${text || res.statusText}`);
        }
        return res.json().catch(() => ({}));
    }

    return (
        <div className="mt-6 flex gap-3">
            <button
                className="rounded-md bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
                disabled={loading}
                onClick={async () => {
                    try {
                        setLoading(true);
                        await callApi('/api/sync/preview'); // dry-run preview
                        router.refresh();
                    } catch (e) {
                        console.error(e);
                        alert('Preview failed. Check logs.');
                    } finally {
                        setLoading(false);
                    }
                }}
            >
                Run Preview
            </button>

            <button
                className="rounded-md bg-emerald-600 px-3 py-2 text-white hover:bg-emerald-700 disabled:opacity-50"
                disabled={loading}
                onClick={async () => {
                    try {
                        setLoading(true);
                        // Full sync (not dry-run). Adjust body if you want purge_bin=false etc.
                        await callApi('/api/sync/full', { dry_run: false });
                        router.refresh();
                    } catch (e) {
                        console.error(e);
                        alert('Full sync failed. Check logs.');
                    } finally {
                        setLoading(false);
                    }
                }}
            >
                Full Sync
            </button>
        </div>
    );
}
