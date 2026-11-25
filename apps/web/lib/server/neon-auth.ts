import { createHash } from "node:crypto";
import { withRetries } from "./retry.ts";

/**
 * Neon Managed Better Auth (server only). The only production auth provider. Nothing here runs unless ENIGMA_NEON_AUTH=on and both
 * NEON_AUTH_BASE_URL (public Auth URL) and NEON_AUTH_COOKIE_SECRET (>= 32 chars, server secret) are valid.
 * The browser never receives a Neon token: the SDK keeps the session in httpOnly cookies and this module mints a short-lived (15 min) EdDSA JWT
 * on the server for the BFF to forward to FastAPI, which verifies it against the Auth URL's JWKS.
 */
/**
 * Revocation policy (see docs/ENIGMA_NEON_EMAIL_VERIFICATION_REPAIR.md): the SDK trusts its signed session_data cookie for this long before asking Neon Auth
 * again, and a minted JWT is reused for at most TOKEN_CACHE_MS. A session revoked elsewhere (other device, password reset, ban) therefore stops working in the
 * console within ~2 minutes; sign-out in THIS browser is immediate. The JWT itself never reaches the browser and is valid at most 15 minutes at the API.
 */
export const SESSION_DATA_TTL_S = 120;
export const TOKEN_CACHE_MS = 5 * 60_000;

export type NeonConfig = { baseUrl: string; secret: string };

export function neonAuthConfig(env: Record<string, string | undefined> = process.env): NeonConfig | null {
  if (env.ENIGMA_NEON_AUTH !== "on") return null;
  const baseUrl = (env.NEON_AUTH_BASE_URL ?? "").replace(/\/$/, "");
  const secret = env.NEON_AUTH_COOKIE_SECRET ?? "";
  try {
    const u = new URL(baseUrl);
    if (u.protocol !== "https:" || u.username || u.password) return null;
  } catch {
    return null;
  }
  return secret.length >= 32 ? { baseUrl, secret } : null;
}

export const neonAuthEnabled = () => neonAuthConfig() !== null;

/** Verified e-mail is required by default (matches the API). Only an explicit "false" relaxes it, e.g. for a local test branch. */
export const neonRequireVerified = (env: Record<string, string | undefined> = process.env) => env.ENIGMA_NEON_REQUIRE_VERIFIED_EMAIL !== "false";

export type SdkError = { status?: number; code?: string; message?: string } | null;
type Res<T> = Promise<{ data: T | null; error: SdkError }>;
type SessionUser = { id?: string; email?: string; name?: string; emailVerified?: boolean };

/**
 * The server methods Enigma uses. `token` and `emailOtp.verifyEmail` exist at runtime (they are in @neon/auth's server endpoint table: GET /token and
 * POST /email-otp/verify-email) but are missing from its TypeScript declarations, so the SDK object is narrowed to this interface instead of `any`.
 * Re-check this list when upgrading @neon/auth.
 */
export interface NeonAuth {
  getSession(): Res<{ user?: SessionUser; session?: unknown }>;
  token(): Res<{ token?: string }>;
  signIn: { email(body: { email: string; password: string }): Res<unknown> };
  signUp: { email(body: { email: string; password: string; name: string }): Res<unknown> };
  signOut(): Res<unknown>;
  emailOtp: { verifyEmail(body: { email: string; otp: string }): Res<{ token?: string | null }> };
  /** POST /send-verification-email: re-sends whichever verification type the branch is configured for (a code, here). */
  sendVerificationEmail(body: { email: string; callbackURL?: string }): Res<unknown>;
}
let instance: { key: string; auth: NeonAuth } | null = null;

export async function getNeonAuth(): Promise<NeonAuth | null> {
  const cfg = neonAuthConfig();
  if (!cfg) return null;
  const key = `${cfg.baseUrl}|${createHash("sha256").update(cfg.secret).digest("hex")}`;
  if (instance?.key === key) return instance.auth;
  const { createNeonAuth } = await import("@neon/auth/next/server");
  const auth = createNeonAuth({ baseUrl: cfg.baseUrl, cookies: { secret: cfg.secret, sessionDataTtl: SESSION_DATA_TTL_S }, logLevel: "error" }) as unknown as NeonAuth;
  instance = { key, auth };
  return auth;
}

/** Names of the SDK's session cookies (`__Secure-neon-auth.*` over HTTPS, `neon-auth.*` otherwise). Presence only: this is NOT a validity check. */
export const hasNeonCookie = (names: Iterable<string>): boolean => {
  for (const n of names) if (/^(__Secure-)?neon-auth\./.test(n)) return true;
  return false;
};

// --- minted-token cache -------------------------------------------------------------------------------------------------------------------
// The console polls the BFF every few seconds; minting a JWT per poll would be one Neon Auth round trip each. Cache per SESSION COOKIE (hashed, never
// stored raw), for at most TOKEN_CACHE_MS (5 minutes) and never past the token's own expiry. A signed-out browser has no cookie, so a cached token is unreachable;
// a token cannot outlive its 15-minute `exp` either way. Bounded size, in-process only.
const TOKEN_CACHE_MAX = 500;
const cache = new Map<string, { token: string; until: number }>();

function jwtExpMs(token: string): number {
  try {
    const exp = JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString()).exp;
    return typeof exp === "number" ? exp * 1000 : 0;
  } catch {
    return 0;
  }
}

export const sessionKey = (cookieValue: string) => createHash("sha256").update(cookieValue).digest("hex");

export function cacheGet(key: string, now = Date.now()): string | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (hit.until <= now) {
    cache.delete(key);
    return null;
  }
  return hit.token;
}

export function cachePut(key: string, token: string, now = Date.now()): void {
  const until = Math.min(now + TOKEN_CACHE_MS, jwtExpMs(token) - 60_000);
  if (until <= now) return;
  if (cache.size >= TOKEN_CACHE_MAX) cache.delete(cache.keys().next().value as string);
  cache.set(key, { token, until });
}

export const cacheDrop = (key: string) => void cache.delete(key);
export const cacheClearAll = () => cache.clear();
export const cacheClearForTests = cacheClearAll;

export type NeonUser = { id: string; email: string; name: string; emailVerified: boolean };
export type NeonSessionInfo = { user: NeonUser; token: string };

/** True when an SDK error means "Neon Auth (or its database) is not ready / unreachable", as opposed to "no such session" (401) or a rejected request (4xx). */
export const isUpstreamFailure = (e: SdkError | undefined): boolean => Boolean(e) && (e!.status === undefined || e!.status >= 500);

// Neon Auth is backed by the same serverless Postgres: after it scales to zero its first answer can be a 5xx. Reads are retried briefly instead of being
// mistaken for "signed out". Only read-only SDK calls are retried here; a genuine 401 / absent session is never retried.
const READ_RETRY = { attempts: 3, delaysMs: [400, 1000] } as const;

/** Current Neon session -> user + a server-minted JWT, or null (no/invalid session, unverified e-mail, any upstream failure: fail closed). */
export async function neonSession(cookieNames: string[], cookieValueFor: (name: string) => string | undefined): Promise<NeonSessionInfo | null> {
  if (!hasNeonCookie(cookieNames)) return null;
  const auth = await getNeonAuth();
  if (!auth) return null;
  try {
    const { data: s } = await withRetries(() => auth.getSession(), (r) => isUpstreamFailure(r.error), READ_RETRY);
    const u = s?.user;
    if (!u?.id || typeof u.id !== "string") return null;
    if (neonRequireVerified() && u.emailVerified !== true) return null;
    const tokenCookie = cookieNames.find((n) => /^(__Secure-)?neon-auth\.session_token$/.test(n));
    const key = sessionKey(`${u.id}:${tokenCookie ? (cookieValueFor(tokenCookie) ?? "") : ""}`);
    let token = cacheGet(key);
    if (!token) {
      const t = await withRetries(() => auth.token(), (r) => isUpstreamFailure(r.error), READ_RETRY);
      token = t?.data?.token ?? null;
      if (!token) return null;
      cachePut(key, token);
    }
    return { user: { id: u.id, email: u.email ?? "", name: u.name ?? "", emailVerified: u.emailVerified === true }, token };
  } catch {
    return null;
  }
}
