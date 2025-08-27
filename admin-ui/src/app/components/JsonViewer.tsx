"use client";
import React, { useState } from "react";

function isObject(val: any) {
    return val && typeof val === "object" && !Array.isArray(val);
}

function isArray(val: any) {
    return Array.isArray(val);
}

export function JsonViewer({ data, level = 0 }: { data: any; level?: number }) {
    // Always show JSON by default
    const open = true;
    if (data === null) return <span className="text-gray-500">null</span>;
    if (typeof data !== "object") return <span className="text-blue-800">{JSON.stringify(data)}</span>;


    // Always expand body field
    const bodyOpen = true;
    // Separate body from other fields
    const entries = Object.entries(data);
    const bodyEntry = entries.find(([key]) => key === "body");
    const otherEntries = entries.filter(([key]) => key !== "body");

    return (
        <div style={{ marginLeft: level * 8 }}>
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
                    {/* Render body as always expanded */}
                    {bodyEntry && (
                        <div style={{ marginLeft: 8, fontFamily: 'monospace' }}>
                            <span className="text-gray-700">body:</span>{" "}
                            <JsonViewer data={bodyEntry[1]} level={level + 1} />
                        </div>
                    )}
                </div>
                {isArray(data) ? (
                    <span className="text-purple-700">]</span>
                ) : (
                    <span className="text-purple-700">&#125;</span>
                )}
            </div>
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
