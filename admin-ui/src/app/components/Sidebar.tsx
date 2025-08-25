'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { stripBase, withBase } from '@/app/lib/basePath';

const nav = [
    { name: 'Sync ERPNext -> Woocommerce', href: '/' },
    { name: 'Woocommerce Status', href: '/webhook-status' },
    { name: 'Woocommerce Inbox', href: '/inbox' },
    { name: 'Manual Sync', href: '/backfill' },
    { name: 'Sync Queue', href: '/jobs' },
    { name: 'Audit Log', href: '/audit-log' },
    { name: 'Product Shipping Details', href: '/shipping' },
    { name: 'ERPNext -> Woo Sync Map', href: '/mapping' },
    { name: 'Mapping Configuration', href: '/mapping/config' },
];

export default function Sidebar() {
    const pathname = usePathname() || '/';
    const logicalPath = stripBase(pathname); // '/shipping', '/mapping', or '/'

    // Find the longest matching nav item for the current path
    let activeHref = nav[0].href;
    for (const i of nav) {
        if (logicalPath === i.href || logicalPath.startsWith(i.href + '/')) {
            if (i.href.length > activeHref.length) {
                activeHref = i.href;
            }
        }
    }

    return (
        <aside className="fixed inset-y-0 w-72 bg-[#405A75] text-white">
            {/* Logo */}
            <div className="p-4 flex items-center justify-center">
                <Link className="block w-full" href="/">
                    {/* Use basePath-aware src so it loads from /admin/public */}
                    <img
                        src={withBase("/techniclad-logo.png")}
                        alt="Techniclad"
                        className="w-[90%] h-auto mx-auto"
                        width={288}
                        height={80}
                    />
                </Link>
            </div>

            {/* Nav */}
            <nav className="mt-2 space-y-1">
                {nav.map((i) => {
                    const active = i.href === activeHref;
                    return (
                        <Link
                            key={i.name}
                            href={i.href}
                            className={`block px-4 py-2 rounded-xl ${active ? 'bg-white/20' : 'hover:bg-white/10'}`}
                        >
                            {i.name}
                        </Link>
                    );
                })}
            </nav>
        </aside>
    );
}
