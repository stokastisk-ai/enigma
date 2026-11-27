import { createHash } from "node:crypto";
import { withRetries } from "./retry.ts";

/**
 * Self-hosted Better Auth (server only): the only production auth provider. Nothing here runs unless ENIGMA_AUTH=on and
 * ENIGMA_PUBLIC_ORIGIN (https, or http loopback for local tests), BETTER_AUTH_SECRET (>= 32 chars) and ENIGMA_AUTH_DB_DSN are valid.
 * The browser never receives a token: Better Auth keeps the session in httpOnly cookies and this module mints a short-lived (15 min) EdDSA JWT on the
 * server for the BFF to forward to FastAPI, which verifies it against this app's JWKS (ENIGMA_AUTH_JWKS_URL on the private container network).
 *
 * Revocation policy: the signed session cookie cache is trusted for SESSION_DATA_TTL_S before the session row is re-read, and a minted JWT is reused for
 * at most TOKEN_CACHE_MS. A session revoked elsewhere stops working in the console within ~2 minutes; sign-out in THIS browser is immediate. The JWT
 * never reaches the browser and is valid at most 15 minutes at the API.
 */
export const SESSION_DATA_TTL_S = 120;
export const TOKEN_CACHE_MS = 5 * 60_000;
export const COOKIE_PREFIX = "enigma-auth";

export type AuthConfig = { origin: string; secret: string; dbDsn: string; requireVerified: boolean };

/** Verified e-mail is required by default (matches the API). Only an explicit "false" relaxes it, e.g. for a local test database. */
export const requireVerified = (env: Record<string, string | undefined> = process.env) => env.ENIGMA_AUTH_REQUIRE_VERIFIED_EMAIL !== "false";

export function authConfig(env: Record<string, string | undefined> = process.env): AuthConfig | null {
  if (env.ENIGMA_AUTH !== "on") return null;
  const secret = env.BETTER_AUTH_SECRET ?? "";
  const dbDsn = env.ENIGMA_AUTH_DB_DSN ?? "";
  let origin: string;
  try {
    const u = new URL((env.ENIGMA_PUBLIC_ORIGIN ?? "").replace(/\/$/, ""));
    const loopback = u.hostname === "127.0.0.1" || u.hostname === "localhost";
    if (u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "")) return null;
    if (!(u.protocol === "https:" || (u.protocol === "http:" && loopback))) return null;
    origin = u.origin;
  } catch {
    return null;
  }
  if (secret.length < 32 || !/^postgres(ql)?:\/\//.test(dbDsn)) return null;
  return { origin, secret, dbDsn, requireVerified: requireVerified(env) };
}

export const authEnabled = () => authConfig() !== null;

/** Private beta: public self-service sign-up is OFF unless ENIGMA_PUBLIC_SIGNUP=on (only enable once a trusted mail sender is verified). */
export const publicSignupOpen = (env: Record<string, string | undefined> = process.env) => env.ENIGMA_PUBLIC_SIGNUP === "on";

/** Names of Better Auth's session cookies with Enigma's prefix (`__Secure-` over HTTPS). Presence only: this is NOT a validity check. */
export const hasAuthCookie = (names: Iterable<string>): boolean => {
  for (const n of names) if (/^(__Secure-)?enigma-auth\./.test(n)) return true;
  return false;
};

export type SdkError = { status?: number; code?: string; message?: string } | null;

/** Better Auth throws APIError({ status, body: { code, message } }); anything else (network, pg) is an upstream failure (no status). */
export function toSdkError(e: unknown): SdkError {
  const x = e as { statusCode?: number; status?: number | string; body?: { code?: string; message?: string }; message?: string };
  const status = typeof x?.statusCode === "number" ? x.statusCode : typeof x?.status === "number" ? x.status : undefined;
  return { status, code: x?.body?.code, message: x?.body?.message ?? (status ? x?.message : undefined) };
}

/** True when an error means "the auth service or its database is not ready / unreachable", as opposed to "no such session" or a rejected request. */
export const isUpstreamFailure = (e: SdkError | undefined): boolean => Boolean(e) && (e!.status === undefined || e!.status >= 500);

let instance: { key: string; auth: import("./auth-instance").EnigmaAuth } | null = null;

export async function getAuth() {
  const cfg = authConfig();
  if (!cfg) return null;
  const key = `${cfg.origin}|${createHash("sha256").update(cfg.secret + "\0" + cfg.dbDsn).digest("hex")}`;
  if (instance?.key === key) return instance.auth;
  const { createAuth } = await import("./auth-instance");
  instance = { key, auth: createAuth(cfg) };
  return instance.auth;
}

// --- minted-token cache -------------------------------------------------------------------------------------------------------------------
// The console polls the BFF every few seconds; minting a JWT per poll would be a signing operation and a DB read each. Cache per SESSION COOKIE (hashed,
// never stored raw), for at most TOKEN_CACHE_MS and never past the token's own expiry. Bounded size, in-process only.
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

export type AuthUser = { id: string; email: string; name: string; emailVerified: boolean };
export type AuthSessionInfo = { user: AuthUser; token: string };

// The local PostgreSQL may be restarting (deploy, reboot): reads are retried briefly instead of being mistaken for "signed out". Only read-only calls
// are retried; a genuine "no session" is never retried.
const READ_RETRY = { attempts: 3, delaysMs: [400, 1000] } as const;

async function attempt<T>(fn: () => Promise<T>): Promise<{ data: T | null; error: SdkError }> {
  try {
    return { data: await fn(), error: null };
  } catch (e) {
    return { data: null, error: toSdkError(e) };
  }
}

/** Current session -> user + a server-minted JWT, or null (no/invalid session, unverified e-mail, banned, any upstream failure: fail closed). */
export async function authSession(headers: Headers, cookieNames: string[], cookieValueFor: (name: string) => string | undefined): Promise<AuthSessionInfo | null> {
  if (!hasAuthCookie(cookieNames)) return null;
  const auth = await getAuth();
  if (!auth) return null;
  try {
    const { data: s } = await withRetries(() => attempt(() => auth.api.getSession({ headers })), (r) => isUpstreamFailure(r.error), READ_RETRY);
    const u = s?.user as (AuthUser & { banned?: boolean | null }) | undefined;
    if (!u?.id || typeof u.id !== "string" || u.banned === true) return null;
    if (requireVerified() && u.emailVerified !== true) return null;
    const tokenCookie = cookieNames.find((n) => /^(__Secure-)?enigma-auth\.session_token$/.test(n));
    const key = sessionKey(`${u.id}:${tokenCookie ? (cookieValueFor(tokenCookie) ?? "") : ""}`);
    let token = cacheGet(key);
    if (!token) {
      const t = await withRetries(() => attempt(() => auth.api.getToken({ headers })), (r) => isUpstreamFailure(r.error), READ_RETRY);
      token = t?.data?.token ?? null;
      if (!token) return null;
      cachePut(key, token);
    }
    return { user: { id: u.id, email: u.email ?? "", name: u.name ?? "", emailVerified: u.emailVerified === true }, token };
  } catch {
    return null;
  }
}
