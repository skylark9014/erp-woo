// API helper to fetch webhook event payload details
export async function fetchWebhookPayload(path: string): Promise<any> {
    const res = await fetch(`/api/integration/webhooks/inbox/get?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    return await res.json();
}
