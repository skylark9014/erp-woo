import './globals.css';
import type { Metadata } from 'next';
import Sidebar from './components/Sidebar';

export const metadata: Metadata = {
  title: 'Techniclad Admin',
  description: 'ERPNext → WooCommerce Admin',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full">
        <div className="min-h-screen">
          {/* LEFT SIDEBAR */}
          <Sidebar />

          {/* RIGHT CONTENT */}
          <div className="lg:pl-72">
            <div className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-gray-200 bg-white/70 px-4 backdrop-blur sm:px-6 lg:px-8">
              <div className="text-sm text-gray-1000">ERPNext → WooCommerce</div>
            </div>

            <main className="py-6">
              <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">{children}</div>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
