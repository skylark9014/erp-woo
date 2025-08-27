// API helper to fetch inbox list and payload details
export async function fetchInboxList(): Promise<any[]> {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    const res = await fetch(`${baseUrl}/api/integration/webhooks/inbox/list?kind=raw`, { cache: "no-store" });
    return await res.json();
}

export async function fetchInboxPayload(path: string): Promise<any> {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    const res = await fetch(`${baseUrl}/api/integration/webhooks/inbox/get?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    return await res.json();
}

// TODO: Add replay API helper when backend endpoint is ready
