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
  // Opt-in self-contained Node server bundle (.next-standalone/standalone/server.js) for a Lambda Web Adapter / container-less host.
  // Used only to validate the AWS alternative while Amplify Hosting does not document Next.js 16; the default build is unchanged.
  ...(process.env.ENIGMA_STANDALONE === "1" ? { output: "standalone" as const } : {}),
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
