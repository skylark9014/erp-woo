'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { stripBase, withBase } from '@/app/lib/basePath';

const nav = [
    { name: 'Synchronize', href: '/' },
    { name: 'Shipping', href: '/shipping' },
    { name: 'Mapping', href: '/mapping' },
];

export default function Sidebar() {
    const pathname = usePathname() || '/';
    const logicalPath = stripBase(pathname); // '/shipping', '/mapping', or '/'

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
                    const active =
                        logicalPath === i.href || logicalPath.startsWith(i.href + '/');
                    return (
                        <Link
                            key={i.name}
                            href={i.href}
                            className={`block px-4 py-2 rounded-xl ${active ? 'bg-white/20' : 'hover:bg-white/10'
                                }`}
                        >
                            {i.name}
                        </Link>
                    );
                })}
            </nav>
        </aside>
    );
}
