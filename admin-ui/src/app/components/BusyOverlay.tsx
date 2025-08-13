'use client';

import React from 'react';

type Props = {
    show: boolean;
    title?: string;
    message?: string;
    submessage?: string;
};

export default function BusyOverlay({ show, title, message, submessage }: Props) {
    if (!show) return null;

    return (
        <div
            className="fixed inset-0 z-[2000]"
            role="dialog"
            aria-modal="true"
            aria-live="polite"
            aria-busy="true"
        >
            {/* dimmed backdrop */}
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />

            <div className="absolute inset-0 flex items-center justify-center p-6">
                <div className="w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-black/5">
                    <div className="flex items-start gap-5 p-6">
                        {/* big dark grey spinner */}
                        <div className="relative shrink-0">
                            <span className="sr-only">Loading</span>
                            <div className="h-16 w-16 animate-spin rounded-full border-[6px] border-gray-300 border-t-gray-700" />
                            <div className="pointer-events-none absolute inset-0 m-auto h-16 w-16 animate-ping rounded-full border-[6px] border-gray-400 opacity-20" />
                        </div>

                        <div className="min-w-0">
                            <h3 className="text-base font-semibold text-gray-900">
                                {title ?? 'Working…'}
                            </h3>
                            {message ? (
                                <p className="mt-1 text-sm text-gray-700">{message}</p>
                            ) : null}
                            {submessage ? (
                                <p className="mt-2 text-xs text-gray-500">{submessage}</p>
                            ) : null}
                        </div>
                    </div>
                    {/* removed bottom progress bar */}
                </div>
            </div>
        </div>
    );
}
