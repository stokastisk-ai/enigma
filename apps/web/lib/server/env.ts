import { devAuthEnabled } from "./hosted-env";
import { neonAuthEnabled } from "./neon-auth";

/** Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_ prefix). */

/** True when Neon Managed Better Auth is configured (the only real identity provider). */
export const realAuthConfigured = () => neonAuthEnabled();

/** Dev auth exists only for local mock E2E. It can never be enabled on Railway or in production. */
export const devAuthAllowed = () => devAuthEnabled();

export const apiBase = () => (process.env.ENIGMA_API_URL ?? process.env.ENIGMA_API_BASE_URL ?? "").replace(/\/$/, "");
