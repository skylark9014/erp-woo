// API helper to fetch recent webhook events from backend
export async function fetchRecentWebhookEvents(): Promise<any[]> {
    // Adjust endpoint as needed; here we use the inbox API for raw events
    const res = await fetch("/api/integration/webhooks/inbox/list?kind=raw", { cache: "no-store" });
    const data = await res.json();
    // Return array of event objects (files)
    return data.raw || [];
}
