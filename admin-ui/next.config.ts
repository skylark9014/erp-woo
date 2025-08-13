// next.config.ts
import type { NextConfig } from 'next';

const basePath = (process.env.NEXT_PUBLIC_ADMIN_BASE_PATH || '').replace(/\/$/, '');

const config: NextConfig = {
  // Serve the whole app (and static files) under /admin
  basePath: basePath || undefined,
  assetPrefix: basePath || undefined,

  // Don’t run image optimizer for the logo
  images: { unoptimized: true },
};

export default config;
