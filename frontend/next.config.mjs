/** @type {import('next').NextConfig} */
// API calls go through the runtime proxy in app/api/[...path]/route.ts (reads API_URLS at request time).
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
};
export default nextConfig;
