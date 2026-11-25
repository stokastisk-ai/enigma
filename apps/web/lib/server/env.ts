import { devAuthEnabled } from "./hosted-env";
import { neonAuthEnabled } from "./neon-auth";

/** Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_ prefix). */

/** True when Neon Managed Better Auth is configured (the only real identity provider). */
export const realAuthConfigured = () => neonAuthEnabled();

/** Dev auth exists only for local mock E2E. It can never be enabled on Railway or in production. */
export const devAuthAllowed = () => devAuthEnabled();

/**
 * Public origin of the FastAPI service as seen by a GPU host (the Kaggle connector calls /api/v1/... on it directly; the Next.js domain does NOT serve those routes).
 * Read at RUNTIME from ENIGMA_PUBLIC_API_URL, so no hostname is baked into the image. Returns "" when unset or unsafe: the UI then shows an obvious placeholder
 * instead of silently falling back to the browser origin.
 */
export function publicApiUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = (env.ENIGMA_PUBLIC_API_URL ?? "").trim().replace(/\/+$/, "");
  try {
    const u = new URL(raw);
    if (u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "")) return "";
    const loopback = u.hostname === "127.0.0.1" || u.hostname === "localhost";
    return u.protocol === "https:" || (u.protocol === "http:" && loopback) ? u.origin : "";
  } catch {
    return "";
  }
}

export const apiBase = () => (process.env.ENIGMA_API_URL ?? process.env.ENIGMA_API_BASE_URL ?? "").replace(/\/$/, "");
