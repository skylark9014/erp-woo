'use client';

import React, { useEffect, useState } from 'react';
import BusyOverlay from '@/app/components/BusyOverlay';

type GetResp = {
  ok: boolean;
  valid: boolean;
  error?: string | null;
  content: string;
  path: string;
  mtime: number;
  size: number;
};

export default function ShippingSettingsPage() {
  const [loaded, setLoaded] = useState(false);
  const [orig, setOrig] = useState<string>('{}');
  const [text, setText] = useState<string>('{}');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyMsg, setBusyMsg] = useState<string | undefined>(undefined);

  async function load() {
    setError(null);
    setInfo(null);
    setBusy(true);
    setBusyMsg('Loading shipping parameters…');
    try {
      const res = await fetch('/admin/api/integration/shipping-params', { cache: 'no-store' });
      const json: GetResp = await res.json();
      if (!res.ok || !json.ok) {
        throw new Error(json?.error || `Failed to load (HTTP ${res.status})`);
      }
      const content = json.content ?? '{}';
      setOrig(content);
      setText(content);
      if (json.valid === false && json.error) {
        setInfo(`File exists but is not valid JSON: ${json.error}`);
      }
      setLoaded(true);
    } catch (e: any) {
      setError(e?.message || 'Failed to load shipping parameters.');
    } finally {
      setBusy(false);
      setBusyMsg(undefined);
    }
  }

  useEffect(() => { load(); }, []);

  function prettify() {
    setError(null);
    setInfo(null);
    try {
      const obj = JSON.parse(text);
      setText(JSON.stringify(obj, null, 2));
      setInfo('Formatted JSON.');
    } catch (e: any) {
      setError(`Cannot format: ${e?.message || e}`);
    }
  }

  function validateOnly() {
    setError(null);
    setInfo(null);
    try {
      JSON.parse(text);
      setInfo('JSON is valid.');
    } catch (e: any) {
      setError(`Invalid JSON: ${e?.message || e}`);
    }
  }

  function revert() {
    setText(orig);
    setError(null);
    setInfo('Reverted to last loaded version.');
  }

  async function save() {
    setError(null);
    setInfo(null);
    setBusy(true);
    setBusyMsg('Saving shipping parameters…');
    try {
      // Validate client-side first
      const obj = JSON.parse(text);
      const res = await fetch('/admin/api/integration/shipping-params', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: obj, pretty: true, sort_keys: true }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        throw new Error(json?.detail || json?.error || `Save failed (HTTP ${res.status})`);
      }
      setOrig(json.content ?? JSON.stringify(obj, null, 2));
      setText(json.content ?? JSON.stringify(obj, null, 2));
      setInfo('Saved successfully.');
    } catch (e: any) {
      setError(e?.message || 'Save failed.');
    } finally {
      setBusy(false);
      setBusyMsg(undefined);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-base font-semibold leading-7 text-gray-900">Shipping Parameters</h1>
        <p className="mt-1 text-sm text-gray-600">
          Edit <code className="font-mono">shipping_prams.json</code>. Changes apply to future syncs.
        </p>
      </div>

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>
      ) : null}
      {info ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{info}</div>
      ) : null}

      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <label className="mb-2 block text-sm font-medium text-gray-700">
          JSON contents
        </label>
        <textarea
          className="mt-1 block w-full rounded-md border-gray-300 font-mono text-sm text-gray-900 shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          rows={24}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            onClick={validateOnly}
            className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            Validate
          </button>
          <button
            onClick={prettify}
            className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50"
          >
            Format JSON
          </button>
          <button
            onClick={save}
            disabled={!loaded}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
          >
            Save
          </button>
          <button
            onClick={revert}
            disabled={!loaded}
            className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
          >
            Revert
          </button>
        </div>
      </div>

      <BusyOverlay
        show={busy}
        title="Please wait"
        message={busyMsg}
        submessage="Writing configuration atomically and creating a backup."
      />
    </div>
  );
}
