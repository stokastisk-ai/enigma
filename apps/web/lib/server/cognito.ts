/**
 * Amazon Cognito (user pool) helpers. Server-only; deliberately imports nothing from Next so `node --test` can exercise it.
 *
 * Flow: the BFF calls the Cognito IDP JSON API (public app client, NO client secret, USER_PASSWORD_AUTH enabled) and keeps the
 * resulting tokens in httpOnly cookies. The browser never holds a token. The FastAPI service re-verifies every access token itself;
 * the verification here only gates pages/BFF and derives the display identity.
 */
import { createPublicKey, verify as cryptoVerify, type JsonWebKey } from "node:crypto";

export type CognitoConfig = { region: string; poolId: string; clientId: string; endpoint?: string };

export const COOKIE_ACCESS = "enigma_at";
export const COOKIE_REFRESH = "enigma_rt";

export function cognitoConfig(env: Record<string, string | undefined> = process.env): CognitoConfig | null {
  const region = env.NEXT_PUBLIC_COGNITO_REGION || env.COGNITO_REGION || "";
  const poolId = env.NEXT_PUBLIC_COGNITO_USER_POOL_ID || env.COGNITO_USER_POOL_ID || "";
  const clientId = env.NEXT_PUBLIC_COGNITO_CLIENT_ID || env.COGNITO_APP_CLIENT_ID || "";
  if (!/^[a-z]{2}(-[a-z]+)+-\d$/.test(region) || !/^[\w-]+_[A-Za-z0-9]+$/.test(poolId) || !/^[A-Za-z0-9]{10,64}$/.test(clientId)) return null;
  // A test endpoint is honoured only for loopback and never in production, so it cannot redirect real credentials.
  const ep = env.ENIGMA_COGNITO_TEST_ENDPOINT;
  const endpoint = ep && env.NODE_ENV !== "production" && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/.test(ep) ? ep.replace(/\/$/, "") : undefined;
  return { region, poolId, clientId, endpoint };
}

export const issuerOf = (c: CognitoConfig) => `https://cognito-idp.${c.region}.amazonaws.com/${c.poolId}`;
const idpUrl = (c: CognitoConfig) => c.endpoint ?? `https://cognito-idp.${c.region}.amazonaws.com/`;
const jwksUrl = (c: CognitoConfig) => (c.endpoint ? `${c.endpoint}/.well-known/jwks.json` : `${issuerOf(c)}/.well-known/jwks.json`);

// ------------------------------------------------------------------ IDP calls
export type Fetch = typeof fetch;
type Json = Record<string, unknown>;
export class CognitoError extends Error {
  code: string;
  status: number;
  constructor(code: string, status: number) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

async function idp(c: CognitoConfig, target: string, body: Json, f: Fetch = fetch): Promise<Json> {
  let r: Response;
  try {
    r = await f(idpUrl(c), {
      method: "POST",
      headers: { "content-type": "application/x-amz-json-1.1", "x-amz-target": `AWSCognitoIdentityProviderService.${target}` },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
      cache: "no-store",
    });
  } catch {
    throw new CognitoError("unreachable", 502);
  }
  const text = await r.text();
  let json: Json = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    /* non-JSON error body */
  }
  if (!r.ok) throw new CognitoError(String(json.__type ?? "error").split("#").pop() ?? "error", r.status);
  return json;
}

export type Tokens = { accessToken: string; refreshToken?: string; expiresIn: number };

function tokensOf(res: Json, keepRefresh?: string): Tokens {
  const a = res.AuthenticationResult as { AccessToken?: string; RefreshToken?: string; ExpiresIn?: number } | undefined;
  if (!a?.AccessToken) throw new CognitoError("unsupported_challenge", 400); // NEW_PASSWORD_REQUIRED / MFA are not supported in the MVP
  return { accessToken: a.AccessToken, refreshToken: a.RefreshToken ?? keepRefresh, expiresIn: Number(a.ExpiresIn ?? 3600) };
}

export const signIn = async (c: CognitoConfig, email: string, password: string, f?: Fetch) =>
  tokensOf(await idp(c, "InitiateAuth", { AuthFlow: "USER_PASSWORD_AUTH", ClientId: c.clientId, AuthParameters: { USERNAME: email, PASSWORD: password } }, f));

export const refresh = async (c: CognitoConfig, refreshToken: string, f?: Fetch) =>
  tokensOf(await idp(c, "InitiateAuth", { AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: c.clientId, AuthParameters: { REFRESH_TOKEN: refreshToken } }, f), refreshToken);

export const signUp = (c: CognitoConfig, email: string, password: string, f?: Fetch) =>
  idp(c, "SignUp", { ClientId: c.clientId, Username: email, Password: password, UserAttributes: [{ Name: "email", Value: email }] }, f);

export const confirmSignUp = (c: CognitoConfig, email: string, code: string, f?: Fetch) =>
  idp(c, "ConfirmSignUp", { ClientId: c.clientId, Username: email, ConfirmationCode: code }, f);

/** Revokes the refresh token (and every access token derived from it) at Cognito. Best effort. */
export const revoke = (c: CognitoConfig, refreshToken: string, f?: Fetch) =>
  idp(c, "RevokeToken", { ClientId: c.clientId, Token: refreshToken }, f).catch(() => undefined);

/** Maps Cognito's error type to a message that does not reveal whether an account exists. */
export function publicError(e: unknown): { status: number; code: string; message: string; needsConfirm?: boolean } {
  const code = e instanceof CognitoError ? e.code : "error";
  switch (code) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return { status: 401, code: "invalid_credentials", message: "Incorrect email or password." };
    case "UserNotConfirmedException":
      return { status: 403, code: "not_confirmed", message: "Confirm your email first.", needsConfirm: true };
    case "UsernameExistsException":
      return { status: 409, code: "cannot_sign_up", message: "Could not create that account. Try signing in." };
    case "InvalidPasswordException":
      return { status: 422, code: "weak_password", message: "Password does not meet the policy." };
    case "CodeMismatchException":
    case "ExpiredCodeException":
      return { status: 422, code: "bad_code", message: "That confirmation code is invalid or expired." };
    case "TooManyRequestsException":
    case "LimitExceededException":
      return { status: 429, code: "rate_limited", message: "Too many attempts. Wait a moment and try again." };
    case "unsupported_challenge":
      return { status: 400, code: "unsupported_challenge", message: "This account needs a sign-in step Enigma does not support yet." };
    case "unreachable":
      return { status: 502, code: "idp_unreachable", message: "The identity service is unreachable." };
    default:
      return { status: 400, code: "auth_failed", message: "Authentication failed." };
  }
}

// ------------------------------------------------------------------ JWT verification (RS256, node:crypto)
type Jwks = { keys: (JsonWebKey & { kid?: string })[] };
const jwksCache = new Map<string, { at: number; jwks: Jwks }>();

async function getJwks(c: CognitoConfig, f: Fetch, force = false): Promise<Jwks> {
  const url = jwksUrl(c);
  const hit = jwksCache.get(url);
  if (hit && !force && Date.now() - hit.at < 3_600_000) return hit.jwks;
  const r = await f(url, { signal: AbortSignal.timeout(5_000), cache: "no-store" });
  if (!r.ok) throw new Error("jwks_unavailable");
  const jwks = (await r.json()) as Jwks;
  jwksCache.set(url, { at: Date.now(), jwks });
  return jwks;
}
export const _clearJwksCache = () => jwksCache.clear();

export type Verified = { sub: string; exp: number };

const b64json = (s: string): Record<string, unknown> => JSON.parse(Buffer.from(s, "base64url").toString("utf8"));

/** Returns the verified identity, or null for ANY problem. Enforces alg, signature, iss, exp, token_use=access and app-client binding. */
export async function verifyAccessToken(token: string, c: CognitoConfig, f: Fetch = fetch, nowS = Math.floor(Date.now() / 1000)): Promise<Verified | null> {
  try {
    const parts = token.split(".");
    if (parts.length !== 3 || token.length > 8192) return null;
    const header = b64json(parts[0]);
    if (header.alg !== "RS256" || typeof header.kid !== "string") return null; // rejects none/HS256 (algorithm confusion)
    let jwks = await getJwks(c, f);
    let jwk = jwks.keys.find((k) => k.kid === header.kid);
    if (!jwk) {
      jwks = await getJwks(c, f, true); // key rotation: one forced refresh
      jwk = jwks.keys.find((k) => k.kid === header.kid);
    }
    if (!jwk) return null;
    const ok = cryptoVerify("RSA-SHA256", Buffer.from(`${parts[0]}.${parts[1]}`), createPublicKey({ key: jwk, format: "jwk" }), Buffer.from(parts[2], "base64url"));
    if (!ok) return null;
    const p = b64json(parts[1]);
    if (p.iss !== issuerOf(c) || p.token_use !== "access" || p.client_id !== c.clientId) return null;
    if (typeof p.sub !== "string" || !p.sub || typeof p.exp !== "number" || p.exp <= nowS) return null;
    return { sub: p.sub, exp: p.exp };
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ CSRF: state-changing requests must be same-origin
export function sameOrigin(originHeader: string | null, hostHeader: string | null, forwardedHost?: string | null, publicHost?: string | null): boolean {
  if (!originHeader) return false; // browsers always send Origin on cross-site and same-site POSTs; absence means a non-browser or stripped request
  try {
    const o = new URL(originHeader);
    // publicHost (ENIGMA_PUBLIC_HOST) is the operator-configured public hostname. Behind CloudFront -> Lambda Function URL the Host header the app
    // sees is the Function URL's, so the browser's Origin would never match it; an explicit allow-list entry is safer than trusting forwarded headers.
    const hosts = [hostHeader, forwardedHost, publicHost].filter(Boolean) as string[];
    return hosts.includes(o.host);
  } catch {
    return false;
  }
}
