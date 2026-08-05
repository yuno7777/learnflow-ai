/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Server components talk to the API over the internal docker network; the browser uses
  // NEXT_PUBLIC_API_URL. Both are read at runtime, not baked in at build time.
  env: {
    API_URL: process.env.API_URL ?? "http://localhost:8000",
  },
};
export default nextConfig;
