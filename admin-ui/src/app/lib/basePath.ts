// src/app/lib/basePath.ts

const CANDIDATE_BASE = "/admin";

/** Returns the active base path ('' or '/admin'). */
export function getBase(): string {
    if (typeof window !== "undefined") {
        return window.location.pathname.startsWith(CANDIDATE_BASE) ? CANDIDATE_BASE : "";
    }
    // On the server we default to the configured base
    return CANDIDATE_BASE;
}

/** Prefix a relative path with the basePath (client-safe). */
export function withBase(path: string): string {
    if (!path) return path;
    if (/^https?:\/\//i.test(path)) return path; // absolute URL
    const rel = path.startsWith("/") ? path : `/${path}`;
    const base = getBase();
    // Already prefixed?
    if (base && rel.startsWith(base + "/")) return rel;
    return base ? `${base}${rel}` : rel;
}

/** Remove the basePath from a pathname (useful for active link matching). */
export function stripBase(pathname: string): string {
    if (!pathname) return "/";
    const base = getBase();
    if (base && pathname.startsWith(base)) {
        const rest = pathname.slice(base.length) || "/";
        return rest.startsWith("/") ? rest : `/${rest}`;
    }
    return pathname;
}
