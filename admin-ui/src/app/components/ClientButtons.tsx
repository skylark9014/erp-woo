'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { startFullSyncAsync, getFullSyncStatus, type SyncJob } from '@/app/lib/api';

function Spinner() {
    return (
        <svg className="animate-spin -ml-0.5 mr-2 h-4 w-4 inline-block" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" fill="currentColor" />
        </svg>
    );
}

export default function ClientButtons() {
    const router = useRouter();

    // keep preview loading separate from full sync loading
    const [previewLoading, setPreviewLoading] = useState(false);

    const [fullLoading, setFullLoading] = useState(false);
    const [phase, setPhase] = useState<'idle' | 'starting' | 'queued' | 'running'>('idle');
    const [jobId, setJobId] = useState<string | null>(null);
    const [jobStatus, setJobStatus] = useState<SyncJob['status'] | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const timerRef = useRef<NodeJS.Timeout | null>(null);

    useEffect(() => {
        return () => {
            if (abortRef.current) abortRef.current.abort();
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, []);

    async function callSimple(path: string, body?: unknown) {
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
            {/* PREVIEW (unchanged) */}
            <button
                className="rounded-md bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
                disabled={previewLoading || fullLoading}
                onClick={async () => {
                    try {
                        setPreviewLoading(true);
                        await callSimple('/api/sync/preview'); // dry-run preview
                        router.refresh();
                    } catch (e) {
                        console.error(e);
                        alert('Preview failed. Check logs.');
                    } finally {
                        setPreviewLoading(false);
                    }
                }}
            >
                {previewLoading ? <Spinner /> : null}
                Run Preview
            </button>

            {/* FULL SYNC (async job with polling) */}
            <button
                className="rounded-md bg-emerald-600 px-3 py-2 text-white hover:bg-emerald-700 disabled:opacity-50"
                disabled={previewLoading || fullLoading}
                onClick={async () => {
                    try {
                        setFullLoading(true);
                        setPhase('starting');

                        const start = await startFullSyncAsync({ dryRun: false, purgeBin: true });

                        // If the backend ran synchronously (200), finish immediately
                        if (start.kind === 'sync') {
                            alert('Full sync completed 🎉');
                            router.refresh();
                            return;
                        }

                        // Otherwise we have a job id — now poll
                        setPhase('queued');
                        setJobId(start.job_id);

                        let delay = 800;
                        while (true) {
                            try {
                                const s = await getFullSyncStatus(start.job_id);
                                setJobStatus(s.status);
                                if (s.status === 'running') setPhase('running');
                                if (s.status === 'done') {
                                    alert('Full sync completed 🎉');
                                    router.refresh();
                                    break;
                                }
                                if (s.status === 'error') {
                                    alert(`Full sync failed: ${s.error || 'Unknown error'}`);
                                    break;
                                }
                            } catch {
                                // tolerate transient status errors
                            }
                            await new Promise(r => setTimeout(r, delay));
                            delay = Math.min(delay + 400, 4000);
                        }
                    } catch (e: any) {
                        console.error(e);
                        alert(`Full sync failed to start: ${e?.message || e}`);
                    } finally {
                        setFullLoading(false);
                        setPhase('idle');
                        setJobId(null);
                        setJobStatus(null);
                    }
                }}
            >
                {fullLoading ? <Spinner /> : null}
                {fullLoading
                    ? phase === 'starting'
                        ? 'Starting full sync…'
                        : phase === 'queued'
                            ? `Queued${jobId ? ` (${jobId.slice(0, 6)})` : ''}…`
                            : 'Running full sync…'
                    : 'Full Sync'}
            </button>
        </div>
    );
}
