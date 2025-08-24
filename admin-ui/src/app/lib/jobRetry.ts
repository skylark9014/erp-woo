// API helper to retry a job by job_id
export async function retryJob(jobId: string): Promise<any> {
    const res = await fetch(`/api/sync/retry/${encodeURIComponent(jobId)}`, {
        method: "POST",
        cache: "no-store"
    });
    return await res.json();
}
