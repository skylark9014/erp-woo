// src/app/layout.tsx
import "./globals.css";
import type { Metadata } from "next";
import Sidebar from "@/app/components/Sidebar";

export const metadata: Metadata = {
  title: "Techniclad Admin",
  description: "ERPNext → WooCommerce Admin",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-gray-50 antialiased">
        {/* Fixed sidebar (takes w-72 on lg+) */}
        <Sidebar />

        {/* Page content shifted right by the sidebar on lg+ */}
        <div className="min-h-screen lg:pl-72">
          {/* Top bar */}
          <div className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-gray-200 bg-white/80 px-4 backdrop-blur sm:px-6 lg:px-8">
            <div className="text-sm text-gray-600">WooCommerce → ERPNext</div>
          </div>

          {/* Main content */}
          <main className="py-6">
            <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}
