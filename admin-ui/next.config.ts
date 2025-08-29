// next.config.mjs
/** @type {import('next').NextConfig} */
const nextConfig = {
  // Hard-set to "/admin" (recommended). If you really want env control, read it here.
  basePath: '/admin',
  images: { unoptimized: true }, // we already use <Image ... unoptimized />
  allowedDevOrigins: [
    'http://localhost:3000',
    'https://records.techniclad.co.za'
  ],
};
export default nextConfig;
