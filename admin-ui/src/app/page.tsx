'use client';

import React, { useEffect, useMemo, useState } from 'react';
import BusyOverlay from '@/app/components/BusyOverlay';
import {
  runHealth,
  runPreview,
  runFullSync,
  runPartialSync,
  loadCachedPreview,
  saveCachedPreview,
  clearCachedPreview,
  runDelete, // deletes (Trash) by Woo product ID
} from '@/app/lib/api';
import type { HealthResponse } from '@/app/lib/api';
import type { PreviewItem, PreviewResponse, SyncReport } from '@/app/types/sync';
import { ArrowPathIcon, PlayIcon } from '@heroicons/react/24/outline';

type Gate = 'checking' | 'ready' | 'down';

function formatCount(n: number | undefined) {
  return (n ?? 0).toLocaleString();
}

function flattenUpdates(r: SyncReport): PreviewItem[] {
  const simpleUpdates = r.to_update ?? [];
  const variantUpdates = r.variant_to_update ?? [];
  const simpleCreates = r.to_create ?? [];
  const variantCreates = r.variant_to_create ?? [];
  return [...variantCreates, ...simpleCreates, ...variantUpdates, ...simpleUpdates];
}

function describeHealthProblems(h: HealthResponse | null): string[] {
  if (!h) return ['Backend not reachable.'];
  const msgs: string[] = [];

  if (h.integration && h.integration.ok === false) {
    msgs.push('Integration API is unreachable — check the "erp-woo-integration" container and network.');
  }
  if (h.erpnext && h.erpnext.ok === false) {
    const s = h.erpnext.status;
    if (s === undefined) msgs.push('Connection to ERPNext server cannot be established.');
    else if (s === 401) msgs.push('ERPNext authentication failed — check API key/secret.');
    else if (s === 403) msgs.push('ERPNext responded 403 (forbidden) — check CORS or firewall.');
    else if (s === 404) msgs.push('ERPNext ping endpoint not found (404) — verify ERP_URL is correct.');
    else if (s === 502 || s === 504) msgs.push('ERPNext gateway error — reverse proxy/tunnel may be offline.');
    else msgs.push(`ERPNext returned HTTP ${s} — verify ERP_URL and credentials.`);
  }
  const wp = h.woocommerce;
  if (wp && wp.ok === false) {
    const code = wp.rest_status ?? wp.status;
    if (code === undefined) msgs.push('Connection to WordPress server cannot be established.');
    else if (code === 401) msgs.push('WordPress authentication failed — check WP_USERNAME and WP_APP_PASSWORD.');
    else if (code === 403) msgs.push('WordPress responded 403 — check basic auth/security plugins/firewall.');
    else if (code === 404) msgs.push('WordPress REST 404 — check WP_API_URL and permalinks.');
    else if (code === 502 || code === 504) msgs.push('WordPress gateway error — tunnel or proxy offline.');
    else msgs.push(`WordPress returned HTTP ${code} — verify WP_API_URL and tunnel.`);
  }
  if (!msgs.length && h.ok === false) msgs.push('Connectivity/authentication check failed — inspect logs for details.');
  return msgs;
}

export default function DashboardPage() {
  const [gate, setGate] = useState<Gate>('checking');
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const [data, setData] = useState<PreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());       // SKUs for partial sync (update)
  const [delSelected, setDelSelected] = useState<Set<number>>(new Set()); // Woo product IDs (simple + variable parents)

  // Hydrate with cached preview (so the page isn’t empty on return)
  useEffect(() => {
    const cached = loadCachedPreview();
    if (cached) setData(cached);
  }, []);

  // Health check at mount
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        setLoading(true);
        setLoadingMsg('Checking connectivity and credentials…');
        const h = await runHealth();
        if (cancel) return;
        setHealth(h);
        setGate(h?.ok === true ? 'ready' : 'down');
      } catch {
        if (cancel) return;
        setHealth({ ok: false } as any);
        setGate('down');
      } finally {
        if (cancel) return;
        setLoading(false);
        setLoadingMsg(undefined);
      }
    })();
    return () => { cancel = true; };
  }, []);

  // Auto preview ONLY on first visit this session (and persist it)
  useEffect(() => {
    if (gate !== 'ready') return;
    if (typeof window === 'undefined') return;

    const KEY = 'tl_autopreview_done';
    const done = window.sessionStorage.getItem(KEY);
    if (done) return;

    // guard right away to avoid StrictMode double-run in dev
    window.sessionStorage.setItem(KEY, '1');

    (async () => {
      try {
        setError(null);
        setLoading(true);
        setLoadingMsg('Running preview (dry-run)…');
        const res = await runPreview();
        setData(res as PreviewResponse);
        saveCachedPreview(res as PreviewResponse); // <-- persist first preview
      } catch (e: any) {
        setError(e?.message || 'Failed to load preview.');
      } finally {
        setLoading(false);
        setLoadingMsg(undefined);
      }
    })();
  }, [gate]);

  // Whenever we have fresh data, persist it as the "last preview"
  useEffect(() => {
    if (data?.sync_report) {
      saveCachedPreview(data as PreviewResponse);
    }
  }, [data]);

  const report = data?.sync_report;

  const counts = useMemo(() => {
    const delSimples = report?.to_delete?.length ?? 0;
    const delParents = report?.variant_parents_to_delete?.length ?? 0;
    return {
      toCreate: report?.to_create?.length ?? 0,
      toUpdate: report?.to_update?.length ?? 0,
      synced: report?.already_synced?.length ?? 0,
      vToCreate: report?.variant_to_create?.length ?? 0,
      vToUpdate: report?.variant_to_update?.length ?? 0,
      vSynced: report?.variant_synced?.length ?? 0,
      parents: report?.variant_parents?.length ?? 0,
      errors: report?.errors?.length ?? 0,
      delSimples,
      delParents,
      delTotal: delSimples + delParents,
    };
  }, [report]);

  const updateRows = useMemo(() => (report ? flattenUpdates(report) : []), [report]);

  // Delete candidates: simple products + variable parents (both have Woo product IDs)
  const deleteRows = useMemo(() => {
    const rows: any[] = [];
    (report?.to_delete ?? []).forEach((r: any) => {
      if (r?.woo?.id) rows.push({ ...r, _kind: 'simple' });
    });
    (report?.variant_parents_to_delete ?? []).forEach((r: any) => {
      if (r?.woo?.id) rows.push({ ...r, _kind: 'var_parent' });
    });
    return rows;
  }, [report]);

  // ---------- selection helpers ----------
  function toggleSKU(sku: string, checked: boolean) {
    setSelected((old) => {
      const next = new Set(old);
      if (checked) next.add(sku);
      else next.delete(sku);
      return next;
    });
  }

  function toggleAllSKUsIn(listSkus: string[], checked: boolean) {
    setSelected((old) => {
      const next = new Set(old);
      if (checked) {
        listSkus.forEach((s) => next.add(s));
      } else {
        listSkus.forEach((s) => next.delete(s));
      }
      return next;
    });
  }

  function toggleDelete(id: number, checked: boolean) {
    setDelSelected((old) => {
      const next = new Set(old);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAllDelete(ids: number[], checked: boolean) {
    setDelSelected((old) => {
      const next = new Set(old);
      if (checked) ids.forEach((id) => next.add(id));
      else ids.forEach((id) => next.delete(id));
      return next;
    });
  }

  // ---------- actions ----------
  async function recheckHealthAndMaybePreview() {
    try {
      setError(null);
      setLoading(true);
      setLoadingMsg('Re-checking connectivity and credentials…');
      const h = await runHealth();
      setHealth(h);
      if (!h.ok) {
        setGate('down');
        return;
      }
      setGate('ready');
    } catch (e: any) {
      setGate('down');
      setError(e?.message || 'Health check failed.');
    } finally {
      setLoading(false);
      setLoadingMsg(undefined);
    }
  }

  async function onRefreshPreview() {
    try {
      setError(null);
      setLoading(true);
      setLoadingMsg('Refreshing preview…');
      const res = await runPreview();
      setData(res as PreviewResponse);
      saveCachedPreview(res as PreviewResponse);
      setSelected(new Set());
      setDelSelected(new Set());
    } catch (e: any) {
      setError(e?.message || 'Preview failed.');
    } finally {
      setLoading(false);
      setLoadingMsg(undefined);
    }
  }

  async function onFullSync() {
    try {
      setError(null);
      setLoading(true);

      // Step 1: full sync (spinner ON)
      setLoadingMsg('Executing FULL sync…');
      await runFullSync({ dryRun: false, purgeBin: true });

      // Clear any stale preview cache
      clearCachedPreview();

      // Step 2: immediate preview (spinner STILL ON, new message)
      setLoadingMsg('Generating preview…');
      const post = await runPreview();
      setData(post as PreviewResponse);
      saveCachedPreview(post as PreviewResponse);

      // Housekeeping
      setSelected(new Set());
      setDelSelected(new Set());
    } catch (e: any) {
      setError(e?.message || 'Full sync failed.');
    } finally {
      setLoading(false);
      setLoadingMsg(undefined);
    }
  }

  async function onPartialSync(dryRun: boolean) {
    const skus = Array.from(selected);
    if (!skus.length) {
      setError('No SKUs selected.');
      return;
    }
    try {
      setError(null);
      setLoading(true);
      setLoadingMsg(dryRun ? 'Running PARTIAL preview…' : 'Executing PARTIAL sync…');

      const res = await runPartialSync({ skus, dryRun });
      setData(res as PreviewResponse);

      if (dryRun) {
        saveCachedPreview(res as PreviewResponse);
      } else {
        clearCachedPreview();
        const post = await runPreview();
        setData(post as PreviewResponse);
        saveCachedPreview(post as PreviewResponse);
        setSelected(new Set());
        setDelSelected(new Set());
      }
    } catch (e: any) {
      setError(e?.message || 'Partial sync failed.');
    } finally {
      setLoading(false);
      setLoadingMsg(undefined);
    }
  }

  // Delete selected Woo products (moves to Trash, not permanent delete)
  async function onDeleteSelected() {
    const ids = Array.from(delSelected);
    if (!ids.length) {
      setError('No products selected for deletion.');
      return;
    }
    try {
      setError(null);
      setLoading(true);
      setLoadingMsg('Deleting selected products in WooCommerce…');

      await runDelete({ ids, force: false }); // Trash (safe)

      // Refresh preview after deletion so the list updates
      clearCachedPreview();
      const post = await runPreview();
      setData(post as PreviewResponse);
      saveCachedPreview(post as PreviewResponse);
      setDelSelected(new Set());
    } catch (e: any) {
      setError(e?.message || 'Delete failed.');
    } finally {
      setLoading(false);
      setLoadingMsg(undefined);
    }
  }

  const totalSynced = (counts.synced ?? 0) + (counts.vSynced ?? 0);
  const healthProblems = gate === 'down' ? describeHealthProblems(health) : [];

  return (
    <div className="space-y-8">
      {/* page header */}
      <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-base font-semibold leading-7 text-gray-900">Dashboard</h1>
          <p className="mt-1 text-sm text-gray-600">
            ERPNext → WooCommerce. Run a preview, review changes, then run a full or partial sync.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefreshPreview}
            disabled={gate !== 'ready'}
            className="inline-flex items-center gap-1 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
          >
            <ArrowPathIcon className="h-4 w-4" />
            Preview
          </button>
          <button
            onClick={onFullSync}
            disabled={gate !== 'ready'}
            className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
          >
            <PlayIcon className="h-4 w-4" />
            Full Sync
          </button>
        </div>
      </div>

      {/* connectivity problems */}
      {gate === 'down' ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <div className="mb-1 font-semibold">Connectivity issues detected:</div>
          <ul className="list-disc pl-5 space-y-1">
            {healthProblems.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
          <div className="mt-3">
            <button
              onClick={recheckHealthAndMaybePreview}
              className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-amber-800 ring-1 ring-inset ring-amber-300 hover:bg-amber-100"
            >
              Retry health check
            </button>
          </div>
        </div>
      ) : null}

      {/* status / error */}
      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>
      ) : null}

      {/* summary cards */}
      <section aria-labelledby="summary-heading">
        <h2 id="summary-heading" className="sr-only">Summary</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryCard label="Simple Products to Create" value={formatCount(counts.toCreate)} />
          <SummaryCard label="Simple Products to Update" value={formatCount(counts.toUpdate)} />
          <SummaryCard label="Variant Products to Create" value={formatCount(counts.vToCreate)} />
          <SummaryCard label="Variant Products to Update" value={formatCount(counts.vToUpdate)} />
          <SummaryCard label="Already Synced" value={formatCount(totalSynced)} />
          <SummaryCard label="Variant Product Parents" value={formatCount(counts.parents)} />
          <SummaryCard label="Errors" value={formatCount(counts.errors)} />
          <SummaryCard label="Products to Delete" value={formatCount(counts.delTotal)} />
        </div>
      </section>

      {/* update list (simples + variants) */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Items requiring updates</h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => onPartialSync(false)}
              disabled={loading || !selected.size || gate !== 'ready'}
              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-emerald-500 disabled:opacity-50"
              title="Run partial sync for selected SKUs"
            >
              Run Selective Sync
            </button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <Th className="w-12">
                  <input
                    type="checkbox"
                    aria-label="Select all update"
                    className="h-4 w-4 rounded border-gray-300"
                    checked={!!updateRows.length && updateRows.every((r: any) => selected.has((r as any).sku))}
                    onChange={(e) => {
                      const listSkus = updateRows.map((r: any) => (r as any).sku).filter(Boolean);
                      toggleAllSKUsIn(listSkus, e.currentTarget.checked);
                    }}
                  />
                </Th>
                <Th>SKU</Th>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Fields</Th>
                <Th className="text-right">Price</Th>
                <Th className="text-right">Stock</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {updateRows.map((row: any) => {
                const isVariant = !!row?.has_variants;
                const fields = Array.isArray(row?.fields_to_update)
                  ? row.fields_to_update.join(', ')
                  : row?.fields_to_update ?? '';
                return (
                  <tr key={(row as any).sku} className="hover:bg-gray-50">
                    <Td className="w-12">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-gray-300"
                        checked={selected.has((row as any).sku)}
                        onChange={(e) => toggleSKU((row as any).sku, e.currentTarget.checked)}
                      />
                    </Td>
                    <Td className="font-mono text-xs text-gray-900">{row?.sku}</Td>
                    <Td className="text-sm text-gray-900">{row?.name ?? '—'}</Td>
                    <Td className="text-xs uppercase text-gray-500">{isVariant ? 'Variant' : 'Simple'}</Td>
                    <Td className="text-xs text-gray-700">{fields || '—'}</Td>
                    <Td className="text-right tabular-nums text-sm text-gray-900">{row?.regular_price ?? '—'}</Td>
                    <Td className="text-right tabular-nums text-sm text-gray-700">{row?.stock_quantity ?? '—'}</Td>
                  </tr>
                );
              })}
              {!updateRows.length ? (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-sm text-gray-500">
                    Nothing to update — click Preview to refresh.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {/* deletes table (simple + variable parents) */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Products to delete in WooCommerce</h3>
          <div className="flex items-center gap-2">
            <button
              onClick={onDeleteSelected}
              disabled={loading || !delSelected.size || gate !== 'ready'}
              className="inline-flex items-center gap-1 rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-500 disabled:opacity-50"
              title="Move selected Woo products to Trash"
            >
              Delete Selected
            </button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <Th className="w-12">
                  <input
                    type="checkbox"
                    aria-label="Select all for delete"
                    className="h-4 w-4 rounded border-gray-300"
                    checked={!!deleteRows.length && deleteRows.every((r: any) => delSelected.has(Number(r?.woo?.id)))}
                    onChange={(e) => {
                      const ids = deleteRows.map((r: any) => Number(r?.woo?.id)).filter((n) => Number.isFinite(n));
                      toggleAllDelete(ids, e.currentTarget.checked);
                    }}
                  />
                </Th>
                {/* reordered: SKU, Name, Type */}
                <Th>SKU</Th>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Woo ID</Th>
                <Th>Reason</Th>
                <Th>Action</Th>
                <Th>Woo Status</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {deleteRows.map((row: any) => {
                const id = Number(row?.woo?.id);
                const kind = row?._kind === 'var_parent' ? 'Variable Parent' : 'Simple';
                return (
                  <tr key={id} className="hover:bg-gray-50">
                    <Td className="w-12">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-gray-300"
                        checked={delSelected.has(id)}
                        onChange={(e) => toggleDelete(id, e.currentTarget.checked)}
                      />
                    </Td>
                    {/* match header order: SKU → Name → Type */}
                    <Td className="font-mono text-xs text-gray-900">{row?.sku ?? '—'}</Td>
                    <Td className="text-sm text-gray-900">{row?.name ?? '—'}</Td>
                    <Td className="text-xs uppercase text-gray-500">{kind}</Td>
                    <Td className="font-mono text-xs text-gray-700">{id || '—'}</Td>
                    <Td className="text-xs text-gray-700">{row?.reason ?? '—'}</Td>
                    {/* planned action (what we will do) */}
                    <Td className="text-xs font-medium text-red-700">Trash</Td>
                    {/* current Woo status for context (often "publish") */}
                    <Td className="text-xs text-gray-500">{row?.woo?.status ?? '—'}</Td>
                  </tr>
                );
              })}
              {!deleteRows.length ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-sm text-gray-500">
                    Nothing to delete — run Preview to refresh.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-500">
          Deletes move products to the WooCommerce Trash (safe).
        </p>
      </section>

      <BusyOverlay
        show={loading}
        title="Please wait"
        message={loadingMsg}
        submessage="This may take a minute depending on images, products, and Woo response times."
      />
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value?: string | number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm text-gray-600">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-gray-900">{value ?? '—'}</div>
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
