// API helper to replay an archived webhook payload
export async function replayInboxPayload(path: string): Promise<any> {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    const res = await fetch(`${baseUrl}/api/integration/webhooks/inbox/replay?path=${encodeURIComponent(path)}`, {
        method: "POST",
        cache: "no-store"
    });
    return await res.json();
}
