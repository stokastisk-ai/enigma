/** Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_ prefix). */
export const clerkConfigured = () =>
  Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

/** Dev auth exists only for local mock E2E. It can never be enabled on Vercel or when Clerk is configured for real use in production. */
export const devAuthAllowed = () =>
  process.env.ENIGMA_DEV_AUTH === "1" &&
  !process.env.VERCEL &&
  !process.env.VERCEL_ENV &&
  (process.env.ENIGMA_DEV_JWT_SECRET ?? "").length >= 32;

export const apiBase = () => (process.env.ENIGMA_API_URL ?? "").replace(/\/$/, "");
