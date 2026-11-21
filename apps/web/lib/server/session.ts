import { createHmac } from "node:crypto";
import { cookies, headers } from "next/headers";
import { clerkConfigured, cognitoSelected, devAuthAllowed } from "./env";
import { COOKIE_ACCESS, COOKIE_REFRESH, cognitoConfig, refresh, verifyAccessToken, type Tokens } from "./cognito";

export type Session = { userId: string; token: string; mode: "clerk" | "cognito" | "dev" };

const b64 = (o: unknown) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

/** HS256 token for the local FastAPI in dev mode only (the API rejects HS256 unless ENIGMA_DEV_AUTH is on and not on Vercel/prod). */
export function mintDevToken(sub: string, secret: string): string {
  const head = b64({ alg: "HS256", typ: "JWT" });
  const body = b64({ sub, aud: "enigma-dev", exp: Math.floor(Date.now() / 1000) + 300 });
  const sig = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${sig}`;
}

/** Cookie attributes for the Cognito tokens: httpOnly (no JS access), SameSite=Lax, Secure outside local http. */
export function authCookieOptions(maxAgeS: number, path = "/") {
  return { httpOnly: true, sameSite: "lax" as const, secure: process.env.NODE_ENV === "production", path, maxAge: maxAgeS };
}

export async function setAuthCookies(t: Tokens) {
  const jar = await cookies();
  jar.set(COOKIE_ACCESS, t.accessToken, authCookieOptions(Math.max(60, t.expiresIn)));
  if (t.refreshToken) jar.set(COOKIE_REFRESH, t.refreshToken, authCookieOptions(30 * 24 * 3600, "/api"));
}

export async function clearAuthCookies() {
  const jar = await cookies();
  jar.set(COOKIE_ACCESS, "", authCookieOptions(0));
  jar.set(COOKIE_REFRESH, "", authCookieOptions(0, "/api"));
}

async function cognitoSession(): Promise<Session | null> {
  const cfg = cognitoConfig();
  if (!cfg) return null;
  const jar = await cookies();
  const at = jar.get(COOKIE_ACCESS)?.value;
  let who = at ? await verifyAccessToken(at, cfg) : null;
  if (who && at) return { userId: `cognito:${who.sub}`, token: at, mode: "cognito" };
  // Access token missing/expired: try the refresh token. Cookies can only be written in route handlers/actions, so a failure to
  // persist the new tokens (e.g. inside a server component) just means we refresh again on the next request.
  const rt = jar.get(COOKIE_REFRESH)?.value;
  if (!rt) return null;
  try {
    const t = await refresh(cfg, rt);
    who = await verifyAccessToken(t.accessToken, cfg);
    if (!who) return null;
    try {
      await setAuthCookies(t);
    } catch {
      /* read-only cookie context */
    }
    return { userId: `cognito:${who.sub}`, token: t.accessToken, mode: "cognito" };
  } catch {
    return null;
  }
}

export async function getSession(): Promise<Session | null> {
  if (cognitoSelected()) return cognitoSession();
  if (clerkConfigured()) {
    const { auth } = await import("@clerk/nextjs/server");
    const { userId, getToken } = await auth();
    if (!userId) return null;
    const token = await getToken();
    return token ? { userId, token, mode: "clerk" } : null;
  }
  if (devAuthAllowed()) {
    // optional dev identity switch so E2E can prove tenant isolation with two users
    const jar = await cookies();
    const who = jar.get("enigma_dev_user")?.value ?? "";
    const userId = /^[a-z0-9_-]{3,40}$/.test(who) ? who : "dev-user-1";
    return { userId, token: mintDevToken(userId, process.env.ENIGMA_DEV_JWT_SECRET!), mode: "dev" };
  }
  return null;
}

export const requestHost = async () => {
  const h = await headers();
  return { host: h.get("host"), forwarded: h.get("x-forwarded-host") };
};
