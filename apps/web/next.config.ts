import type { NextConfig } from "next";

// Authenticated, client-driven console: no static shells / cache components (they add constraints without benefit here).
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // The mock E2E suite builds into its own directory with Clerk disabled, so it never shares a build with the real-auth configuration.
  distDir: process.env.ENIGMA_DIST_DIR || ".next",
  poweredByHeader: false,
  // Self-contained Node server bundle (.next/standalone/server.js) used by apps/web/Dockerfile on Railway (ENIGMA_STANDALONE=1); the default build is unchanged.
  // images.unoptimized: Enigma uses no next/image, and without it Next does not bundle the native `sharp` binary, so the image stays pure JavaScript.
  // Re-enable optimisation deliberately if next/image is ever added.
  ...(process.env.ENIGMA_STANDALONE === "1" ? { output: "standalone" as const, images: { unoptimized: true }, outputFileTracingExcludes: { "*": ["node_modules/sharp/**", "node_modules/@img/**"] } } : {}),
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
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
