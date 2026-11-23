import { cognitoConfig } from "./cognito";
import { devAuthEnabled } from "./hosted-env";

/** Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_ prefix). */

/** Which identity provider is selected. Cognito (AWS) wins when ENIGMA_AUTH_PROVIDER=cognito and its public identifiers are valid. */
export const cognitoSelected = () => process.env.ENIGMA_AUTH_PROVIDER === "cognito" && cognitoConfig() !== null;

/** Clerk is the historical provider; it is ignored whenever Cognito is selected so the two can never both be active. */
export const clerkConfigured = () =>
  !cognitoSelected() && Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

/** True when a real identity provider (Cognito or Clerk) is configured. */
export const realAuthConfigured = () => cognitoSelected() || clerkConfigured();

/** Dev auth exists only for local mock E2E. It can never be enabled on Vercel/AWS/Railway-hosted production or when a real provider is configured. */
export const devAuthAllowed = () => devAuthEnabled();

export const apiBase = () => (process.env.ENIGMA_API_URL ?? process.env.ENIGMA_API_BASE_URL ?? "").replace(/\/$/, "");
