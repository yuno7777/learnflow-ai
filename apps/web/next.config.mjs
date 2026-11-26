/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The floating dev badge sits bottom-left, directly over the sidebar nav.
  devIndicators: false,
  output: "standalone",
  // `next build` writes into the same directory `next dev` serves from, so a verification
  // build run against a live dev server leaves it serving 404s for every chunk. Set
  // NEXT_DIST_DIR to build somewhere else while the dev server keeps running.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // Server components talk to the API over the internal docker network; the browser uses
  // NEXT_PUBLIC_API_URL. Both are read at runtime, not baked in at build time.
  env: {
    API_URL: process.env.API_URL ?? "http://localhost:8000",
  },
};
export default nextConfig;
