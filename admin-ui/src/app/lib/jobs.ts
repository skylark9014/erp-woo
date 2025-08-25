// API helper to fetch jobs list and job details
export async function fetchJobsList(): Promise<any[]> {
    // Use withBase to ensure correct routing for admin UI
    const { withBase } = await import("@/app/lib/basePath");
    // Get admin credentials from environment variables
    // Hardcoded credentials for quick test
    const basicAuth = "Basic " + btoa("scott@axxess.co.za:@Axxess1968");
    const res = await fetch("/api/sync/jobs", {
        cache: "no-store",
        headers: {
            "Authorization": basicAuth
        }
    });
    let data: any = null;
    try {
        data = await res.json();
    } catch {
        // If response is not JSON, return empty list and log error
        console.error("Jobs API returned non-JSON response", await res.text());
        return [];
    }
    if (!res.ok) {
        let errorText = `Jobs API returned ${res.status}`;
        try {
            // Only read the body once
            errorText += ": " + (await res.text());
        } catch { }
        throw new Error(errorText);
    }
    return data.jobs || [];
}

export async function fetchJobPayload(jobId: string): Promise<any> {
    const { withBase } = await import("@/app/lib/basePath");
    const res = await fetch(withBase(`/api/integration/jobs/get?job_id=${encodeURIComponent(jobId)}`), { cache: "no-store" });
    let data: any = null;
    try {
        data = await res.json();
    } catch {
        console.error("Job payload API returned non-JSON response", await res.text());
        return null;
    }
    return data;
}

// TODO: Add retry API helper when backend endpoint is ready
