// API helper to set status for a payload file
export async function setInboxStatus(path: string, status: string): Promise<any> {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    const res = await fetch(`${baseUrl}/api/integration/webhooks/inbox/set_status?path=${encodeURIComponent(path)}&status=${encodeURIComponent(status)}`, {
        method: "POST",
    });
    return await res.json();
}
