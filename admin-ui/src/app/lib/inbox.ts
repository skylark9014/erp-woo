// API helper to fetch inbox list and payload details
export async function fetchInboxList(): Promise<any[]> {
    const res = await fetch("/api/integration/webhooks/inbox/list?kind=raw", { cache: "no-store" });
    const data = await res.json();
    return data.raw || [];
}

export async function fetchInboxPayload(path: string): Promise<any> {
    const res = await fetch(`/api/integration/webhooks/inbox/get?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    return await res.json();
}

// TODO: Add replay API helper when backend endpoint is ready
