// src/app/components/SummaryCard.tsx
import * as React from 'react';

export default function SummaryCard({
    label,
    value,
}: {
    label: string;
    value?: string | number;
}) {
    return (
        <div className="rounded-lg border border-gray-200 bg-white p-4" >
            <div className="text-sm text-gray-600" > {label} </div>
            <div className="mt-1 text-2xl font-semibold text-gray-900" >
                {value ?? '—'}</div></div>
    );
}
