"use client";
import React, { useState } from "react";

function isObject(val: any) {
    return val && typeof val === "object" && !Array.isArray(val);
}

function isArray(val: any) {
    return Array.isArray(val);
}

export function JsonViewer({ data, level = 0 }: { data: any; level?: number }) {
    // Hide JSON by default until user clicks 'Show JSON'
    const [open, setOpen] = useState(false);
    if (data === null) return <span className="text-gray-500">null</span>;
    if (typeof data !== "object") return <span className="text-blue-800">{JSON.stringify(data)}</span>;

    // Present only the JSON headers content in the summary
    let summaryText = "No summary available";
    let headerParts: string[] = [];
    if (data.headers && typeof data.headers === "object") {
        headerParts = Object.entries(data.headers)
            .filter(([_, value]) => typeof value === "string" || typeof value === "number" || typeof value === "boolean")
            .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`);
    }
    if (headerParts.length > 0) {
        summaryText = headerParts.join(" | ");
    } else {
        // Fallback: show top-level key-value pairs (excluding objects/arrays)
        const topLevelParts = Object.entries(data)
            .filter(([_, value]) => typeof value === "string" || typeof value === "number" || typeof value === "boolean")
            .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`);
        if (topLevelParts.length > 0) {
            summaryText = topLevelParts.join(" | ");
        }
    }

    // Track body expand/collapse state
    const [bodyOpen, setBodyOpen] = useState(false);
    // Separate body from other fields
    const entries = Object.entries(data);
    const bodyEntry = entries.find(([key]) => key === "body");
    const otherEntries = entries.filter(([key]) => key !== "body");

    return (
        <div style={{ marginLeft: level * 8 }}>
            {level === 0 && (
                <div className="mb-2">
                    <span className="font-bold text-gray-800">Summary:</span>
                    <span className="ml-2 text-gray-700">{summaryText}</span>
                    <button
                        className="ml-4 text-xs text-blue-600 underline cursor-pointer"
                        onClick={() => setOpen(o => !o)}
                    >
                        [{open ? "Hide JSON" : "Show JSON"}]
                    </button>
                </div>
            )}
            {open && (
                <div className="border-l-2 border-blue-200 pl-2">
                    {isArray(data) ? (
                        <span className="text-purple-700">[</span>
                    ) : (
                        <span className="text-purple-700">&#123;</span>
                    )}
                    <div>
                        {/* Render all fields except body */}
                        {otherEntries.map(([key, value], idx) => (
                            <JsonNode key={key + idx} k={key} v={value} level={level + 1} />
                        ))}
                        {/* Render body as expandable */}
                        {bodyEntry && (
                            <div style={{ marginLeft: 8, fontFamily: 'monospace' }}>
                                <span className="text-gray-700">body:</span>{" "}
                                <button
                                    className="text-xs text-blue-600 underline cursor-pointer mr-1"
                                    onClick={() => setBodyOpen((o) => !o)}
                                >
                                    [{bodyOpen ? "Hide" : "Show"}]
                                </button>
                                {bodyOpen ? <JsonViewer data={bodyEntry[1]} level={level + 1} /> : <span className="text-gray-400">[hidden]</span>}
                            </div>
                        )}
                    </div>
                    {isArray(data) ? (
                        <span className="text-purple-700">]</span>
                    ) : (
                        <span className="text-purple-700">&#125;</span>
                    )}
                </div>
            )}
        </div>
    );
}

function JsonNode({ k, v, level }: { k: string; v: any; level: number }) {
    const [open, setOpen] = useState(level < 2); // expand top 2 levels by default
    const isObj = isObject(v) || isArray(v);
    return (
        <div style={{ marginLeft: 8, fontFamily: 'monospace' }}>
            <span className="text-gray-700">{k}:</span>{" "}
            {isObj ? (
                <span>
                    <button
                        className="text-xs text-blue-600 underline cursor-pointer mr-1"
                        onClick={() => setOpen((o) => !o)}
                    >
                        [{open ? "-" : "+"}]
                    </button>
                    {open ? <JsonViewer data={v} level={level} /> : <span className="text-gray-400">{isArray(v) ? "[Array]" : "{Object}"}</span>}
                </span>
            ) : (
                <span className="text-green-700">{JSON.stringify(v)}</span>
            )}
        </div>
    );
}
