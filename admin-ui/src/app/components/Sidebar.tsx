'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
// ⬇️ use ComponentType instead of JSX.Element
import type { ComponentType, SVGProps } from 'react';

import {
    HomeIcon,
    ArrowPathIcon,
    Cog6ToothIcon,
} from '@heroicons/react/24/outline';

type NavItem = {
    href: string;
    label: string;
    icon: ComponentType<SVGProps<SVGSVGElement>>;
    current?: boolean;
};


const mainNav: NavItem[] = [
    { href: '/', label: 'Dashboard', icon: HomeIcon, current: true },
    { href: '/', label: 'Run Preview / Sync', icon: ArrowPathIcon },
];

const footerNav: NavItem[] = [
    { href: '/settings/shipping', label: 'Settings', icon: Cog6ToothIcon },
];

export default function Sidebar() {
    return (
        <div className="hidden lg:fixed lg:inset-y-0 lg:z-50 lg:flex lg:w-72 lg:flex-col"
            style={{ backgroundColor: '#405A75' }}>
            <div className="flex grow flex-col gap-y-5 overflow-y-auto border-r border-gray-200/20 px-6">
                {/* Logo block */}
                <div className="flex h-24 shrink-0 items-center">
                    <div className="w-full flex items-center justify-center px-2">
                        <div className="relative w-[90%]">
                            <Image
                                src="/techniclad-logo.png"
                                alt="Techniclad"
                                width={600}
                                height={200}
                                priority
                                style={{ width: '100%', height: 'auto' }}
                            />
                        </div>
                    </div>
                </div>

                <nav className="flex flex-1 flex-col">
                    <ul role="list" className="flex flex-1 flex-col gap-y-2">
                        {mainNav.map((item) => (
                            <li key={item.label}>
                                <Link
                                    href={item.href}
                                    className={`group flex gap-x-3 rounded-md p-2 text-sm font-semibold leading-6 ${item.current
                                        ? 'bg-white/10 text-white'
                                        : 'text-slate-100 hover:bg-white/10 hover:text-white'
                                        }`}
                                >
                                    <item.icon className={`h-6 w-6 shrink-0 ${item.current ? 'text-white' : 'text-slate-200 group-hover:text-white'
                                        }`} aria-hidden="true" />
                                    {item.label}
                                </Link>
                            </li>
                        ))}

                        {/* footer / bottom */}
                        <li className="mt-auto">
                            {footerNav.map((item) => (
                                <div key={item.label}>
                                    <Link
                                        href={item.href}
                                        className="group flex gap-x-3 rounded-md p-2 text-sm font-semibold leading-6 text-slate-100 hover:bg-white/10 hover:text-white"
                                    >
                                        <item.icon className="h-6 w-6 shrink-0 text-slate-200 group-hover:text-white" aria-hidden="true" />
                                        {item.label}
                                    </Link>
                                </div>
                            ))}
                        </li>
                    </ul>
                </nav>
            </div>
        </div>
    );
}
