import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Every admin page shows live, privileged data for the signed-in admin and needs a per-request CSP nonce, so
  // pages are always rendered on request and nothing is cached or prerendered.
  cacheComponents: false,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
