function authHeader() {
    const u = process.env.INTEGRATION_ADMIN_USER || "";
    const p = process.env.INTEGRATION_ADMIN_PASS || "";
    const token = Buffer.from(`${u}:${p}`).toString("base64");
    return `Basic ${token}`;
}

export async function GET() {
    const base = process.env.INTEGRATION_BASE_URL || "http://integration:8000";
    const r = await fetch(`${base}/api/sync/preview`, {
        method: "GET",
        headers: { Authorization: authHeader() },
        // Next routes are server-side; no-cache helps when you click repeatedly
        cache: "no-store",
    });
    const text = await r.text();
    return new Response(text, {
        status: r.status,
        headers: { "content-type": r.headers.get("content-type") || "application/json" },
    });
}

export async function POST(req: Request) {
    const base = process.env.INTEGRATION_BASE_URL || "http://integration:8000";
    const body = await req.text();
    const r = await fetch(`${base}/api/sync/preview`, {
        method: "POST",
        headers: {
            Authorization: authHeader(),
            "content-type": "application/json",
        },
        body: body || JSON.stringify({ dry_run: true }),
        cache: "no-store",
    });
    const text = await r.text();
    return new Response(text, {
        status: r.status,
        headers: { "content-type": r.headers.get("content-type") || "application/json" },
    });
}
