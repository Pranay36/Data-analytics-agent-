import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server (server.js plus only the modules it uses), so the production
  // image carries no node_modules tree and no build tooling.
  output: "standalone",
};

export default nextConfig;
