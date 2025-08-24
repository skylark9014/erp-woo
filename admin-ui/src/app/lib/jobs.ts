// API helper to fetch jobs list and job details
export async function fetchJobsList(): Promise<any[]> {
    const res = await fetch("/api/sync/jobs", { cache: "no-store" });
    const data = await res.json();
    return data.jobs || [];
}

export async function fetchJobPayload(jobId: string): Promise<any> {
    const res = await fetch(`/api/integration/jobs/get?job_id=${encodeURIComponent(jobId)}`, { cache: "no-store" });
    return await res.json();
}

// TODO: Add retry API helper when backend endpoint is ready
