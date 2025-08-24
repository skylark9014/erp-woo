// API helper to replay an archived webhook payload
export async function replayInboxPayload(path: string): Promise<any> {
    const res = await fetch(`/api/integration/webhooks/inbox/replay?path=${encodeURIComponent(path)}`, {
        method: "POST",
        cache: "no-store"
    });
    return await res.json();
}
