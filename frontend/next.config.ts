import type { NextConfig } from "next";

// Where the API lives, as seen by the Next.js *server* (never by the browser). When set, every
// /api/v1 request the browser sends to this site is forwarded there. The browser only ever talks
// to one origin, so the login cookie is first-party and no CORS is involved.
const backend = process.env.BACKEND_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  // A self-contained server for the Docker image. Vercel has its own build output, so it skips this.
  output: process.env.VERCEL ? undefined : "standalone",

  async rewrites() {
    return backend ? [{ source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` }] : [];
  },
};

export default nextConfig;
