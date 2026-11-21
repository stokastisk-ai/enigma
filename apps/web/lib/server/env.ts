import { cognitoConfig } from "./cognito";

/** Server-only configuration. Nothing here is exposed to the browser (no NEXT_PUBLIC_ prefix). */

/** Which identity provider is selected. Cognito (AWS) wins when ENIGMA_AUTH_PROVIDER=cognito and its public identifiers are valid. */
export const cognitoSelected = () => process.env.ENIGMA_AUTH_PROVIDER === "cognito" && cognitoConfig() !== null;

/** Clerk is the historical provider; it is ignored whenever Cognito is selected so the two can never both be active. */
export const clerkConfigured = () =>
  !cognitoSelected() && Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

/** True when a real identity provider (Cognito or Clerk) is configured. */
export const realAuthConfigured = () => cognitoSelected() || clerkConfigured();

/** Dev auth exists only for local mock E2E. It can never be enabled on Vercel/AWS-hosted production or when a real provider is configured. */
export const devAuthAllowed = () =>
  process.env.ENIGMA_DEV_AUTH === "1" &&
  !process.env.VERCEL &&
  !process.env.VERCEL_ENV &&
  !process.env.AWS_EXECUTION_ENV &&
  !process.env.AWS_LAMBDA_FUNCTION_NAME &&
  (process.env.ENIGMA_DEV_JWT_SECRET ?? "").length >= 32;

export const apiBase = () => (process.env.ENIGMA_API_URL ?? process.env.ENIGMA_API_BASE_URL ?? "").replace(/\/$/, "");
