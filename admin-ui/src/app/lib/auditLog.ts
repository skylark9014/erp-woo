// src/app/lib/auditLog.ts
export type AuditLogEntry = {
    action: string;
    user: string;
    timestamp: string;
    details: string;
};

export async function fetchAuditLog(): Promise<AuditLogEntry[]> {
    const res = await fetch("/admin/api/audit-log", { cache: "no-store" });
    if (!res.ok) return [];
    const data = await res.json();
    return data.entries || [];
}
