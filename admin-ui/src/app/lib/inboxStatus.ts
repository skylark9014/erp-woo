// API helper to set status for a payload file
export async function setInboxStatus(path: string, status: string): Promise<any> {
    const res = await fetch(`/api/integration/webhooks/inbox/set_status?path=${encodeURIComponent(path)}&status=${encodeURIComponent(status)}`, {
        method: "POST",
    });
    return await res.json();
}
